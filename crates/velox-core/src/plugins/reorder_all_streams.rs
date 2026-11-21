//! Order Streams - re-order streams by codec, channels, language and type.

use async_trait::async_trait;

use crate::config::ReorderAllStreamsConfig;
use crate::error::Result;
use crate::media::{stream_type, MediaInfo, CHANNEL_LABELS, VIDEO};

use super::{PipelineContext, Plugin, PluginResult, RemuxPlan};

pub struct ReorderAllStreamsPlugin {
    config: ReorderAllStreamsConfig,
}

impl ReorderAllStreamsPlugin {
    pub fn new(config: ReorderAllStreamsConfig) -> Self {
        Self { config }
    }

    fn sort_value(media: &MediaInfo, index: usize, kind: &str) -> String {
        let Some(stream) = media.streams.iter().find(|stream| stream.index == index) else {
            return String::new();
        };
        match kind {
            "languages" => stream.language(),
            "codecs" => stream.codec(),
            "channels" => stream
                .channels
                .and_then(|channels| {
                    CHANNEL_LABELS
                        .iter()
                        .find(|(value, _)| *value == channels)
                        .map(|(_, label)| (*label).to_string())
                })
                .unwrap_or_default(),
            "streamTypes" => stream_type(stream).to_string(),
            _ => String::new(),
        }
    }

    fn sort_streams(&self, media: &MediaInfo, streams: &mut Vec<usize>, inputs: &str, kind: &str) {
        let mut items: Vec<&str> = inputs
            .split(',')
            .map(|item| item.trim())
            .filter(|item| !item.is_empty())
            .collect();
        items.reverse();

        for item in items {
            let mut matched: Vec<usize> = Vec::new();
            let mut i = 0;
            while i < streams.len() {
                let index = streams[i];
                if Self::sort_value(media, index, kind) == item {
                    let stream = media
                        .streams
                        .iter()
                        .find(|stream| stream.index == index)
                        .expect("stream index");
                    let codec_long = stream.codec_long_name.clone().unwrap_or_default();
                    let codec_name = stream.codec();
                    if !codec_long.is_empty()
                        && (codec_long.contains("image") || codec_name.contains("png"))
                    {
                        i += 1;
                    } else {
                        matched.push(streams.remove(i));
                    }
                } else {
                    i += 1;
                }
            }
            matched.extend(streams.iter().copied());
            *streams = matched;
        }
    }
}

#[async_trait]
impl Plugin for ReorderAllStreamsPlugin {
    fn key(&self) -> &'static str {
        "reorder_all_streams"
    }

    fn name(&self) -> &'static str {
        "Order Streams"
    }

    fn enabled(&self) -> bool {
        self.config.enabled
    }

    async fn run(&self, media: &MediaInfo, _ctx: &PipelineContext) -> Result<PluginResult> {
        let mut result = PluginResult::new(media.container());

        if media.streams.is_empty() {
            result.error = Some(
                "ffprobe was unable to extract any stream info on this file. This may be a corrupt file or a permissions issue."
                    .to_string(),
            );
            return Ok(result);
        }

        if media.container() == "mp4" && media.is_video() {
            if media.first_stream_type() == VIDEO {
                result.note(
                    "File is mp4 and already has the video stream in the correct order! Due to FFmpeg issues when reordering streams in mp4 files, other stream ordering will be skipped.",
                );
                return Ok(result);
            }
            result.process_file = true;
            result.note("File is mp4 and contains video but video is not first stream, remuxing");
            result.remux = Some(RemuxPlan {
                order: Some(media.type_order()),
                ..Default::default()
            });
            result.requeue = true;
            return Ok(result);
        }

        let mut streams: Vec<usize> = media.streams.iter().map(|stream| stream.index).collect();
        let original_order = streams.clone();

        let process_order = self.config.process_order.clone();
        for sort_name in process_order
            .split(',')
            .map(|value| value.trim())
            .filter(|value| !value.is_empty())
        {
            let inputs = match sort_name {
                "languages" => &self.config.languages,
                "codecs" => &self.config.codecs,
                "channels" => &self.config.channels,
                "streamTypes" => &self.config.stream_types,
                _ => continue,
            };
            if inputs.is_empty() {
                continue;
            }
            self.sort_streams(media, &mut streams, inputs, sort_name);
        }

        if streams != original_order {
            result.note("Streams are not in the correct order!");
            result.process_file = true;
            result.remux = Some(RemuxPlan {
                order: Some(streams),
                ..Default::default()
            });
            result.requeue = true;
        } else {
            result.note("Streams are in the correct order!");
        }
        Ok(result)
    }
}
