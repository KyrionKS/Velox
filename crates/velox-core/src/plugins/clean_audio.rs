//! Clean Audio Tracks - keep only the chosen audio languages.

use std::collections::{BTreeMap, BTreeSet};

use async_trait::async_trait;

use crate::config::CleanAudioConfig;
use crate::error::Result;
use crate::media::{MediaInfo, Stream, AUDIO};

use super::{PipelineContext, Plugin, PluginResult, RemuxPlan};

pub struct CleanAudioPlugin {
    config: CleanAudioConfig,
}

impl CleanAudioPlugin {
    pub fn new(config: CleanAudioConfig) -> Self {
        Self { config }
    }

    fn pick_default(kept: &[&Stream], preference: &str) -> Option<usize> {
        if preference == "first" {
            return kept.first().map(|stream| stream.index);
        }
        if preference == "best" {
            let mut best = kept.first()?;
            for stream in kept.iter().skip(1) {
                if stream.channels.unwrap_or(0) > best.channels.unwrap_or(0) {
                    best = stream;
                }
            }
            return Some(best.index);
        }
        kept.iter()
            .find(|stream| {
                let language = stream.language();
                language == preference || (preference == "und" && language.is_empty())
            })
            .map(|stream| stream.index)
    }
}

#[async_trait]
impl Plugin for CleanAudioPlugin {
    fn key(&self) -> &'static str {
        "clean_audio"
    }

    fn name(&self) -> &'static str {
        "Clean Audio Tracks"
    }

    fn enabled(&self) -> bool {
        self.config.enabled
    }

    async fn run(&self, media: &MediaInfo, _ctx: &PipelineContext) -> Result<PluginResult> {
        let mut result = PluginResult::new(media.container());

        if !media.is_video() {
            result.note("File is not video");
            return Ok(result);
        }

        if self.config.language.is_empty() {
            result.note(
                "Language/s options not set, please configure required options. Skipping this plugin.",
            );
            return Ok(result);
        }

        let languages: Vec<String> = self
            .config
            .language
            .split(',')
            .map(|item| item.trim().to_lowercase())
            .collect();
        let commentary = self.config.commentary;
        let tag_language = self.config.tag_language.trim().to_lowercase();
        let tag_title = self.config.tag_title;
        let default_audio = self.config.default_audio.trim().to_lowercase();

        let mut drop: BTreeSet<usize> = BTreeSet::new();
        let mut metadata: BTreeMap<usize, Vec<(String, String)>> = BTreeMap::new();
        let mut kept: Vec<&Stream> = Vec::new();
        let audio_total = media.streams_of(AUDIO).len();

        for stream in media.streams.iter().filter(|s| s.codec_type == AUDIO) {
            let index = stream.index;
            let has_language = stream.tags.contains_key("language");
            let language = stream.language();
            let mut remove = false;

            if has_language && !languages.iter().any(|value| value == &language) {
                remove = true;
                result.note(format!(
                    "Audio stream {index} has unwanted language tag {language}, removing."
                ));
            }

            if commentary && stream.tags.contains_key("title") {
                let title = stream.title().to_lowercase();
                if ["commentary", "description", "sdh"]
                    .iter()
                    .any(|word| title.contains(word))
                {
                    remove = true;
                    result.note(format!(
                        "Audio stream {index} detected as being descriptive, removing."
                    ));
                }
            }

            if remove {
                drop.insert(index);
                continue;
            }

            kept.push(stream);

            if !tag_language.is_empty() && (!has_language || language.contains("und")) {
                metadata
                    .entry(index)
                    .or_default()
                    .push(("language".to_string(), tag_language.clone()));
                result.note(format!(
                    "Audio stream {index} detected as having no language, tagging as {tag_language}."
                ));
            }

            if tag_title && !stream.tags.contains_key("title") {
                if let Some(label) = stream.channels.and_then(MediaInfo::channel_label) {
                    metadata
                        .entry(index)
                        .or_default()
                        .push(("title".to_string(), label.to_string()));
                    result.note(format!(
                        "Audio stream {index} detected as {} channel with no title, tagging.",
                        stream.channels.unwrap_or(0)
                    ));
                }
            }
        }

        // Failsafe: never remove every audio track.
        if audio_total > 0 && drop.len() == audio_total {
            result.process_file = false;
            result.note("Cancelling plugin otherwise all audio tracks would be removed.");
            return Ok(result);
        }

        let mut dispositions: BTreeMap<usize, String> = BTreeMap::new();
        if !default_audio.is_empty() && !kept.is_empty() {
            if let Some(chosen) = Self::pick_default(&kept, &default_audio) {
                for stream in &kept {
                    dispositions.insert(
                        stream.index,
                        if stream.index == chosen {
                            "default".to_string()
                        } else {
                            "0".to_string()
                        },
                    );
                }
                let label = kept
                    .iter()
                    .find(|stream| stream.index == chosen)
                    .map(|stream| {
                        let language = stream.language();
                        if language.is_empty() {
                            "und".to_string()
                        } else {
                            language
                        }
                    })
                    .unwrap_or_else(|| "und".to_string());
                result.note(format!(
                    "Marking audio stream {chosen} ({label}) as the default track."
                ));
            }
        }

        if !drop.is_empty() || !metadata.is_empty() || !dispositions.is_empty() {
            result.process_file = true;
            result.remux = Some(RemuxPlan {
                drop,
                metadata,
                dispositions,
                ..Default::default()
            });
            result.requeue = true;
        } else {
            result.note(
                "File doesn't contain audio tracks which are unwanted or that require tagging.",
            );
        }
        Ok(result)
    }
}
