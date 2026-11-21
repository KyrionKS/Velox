//! Standardise Audio Codec.
//!
//! Transcodes every audio stream that is not already in the target codec.  The
//! source channel count is passed explicitly: AC-3/E-AC-3/DTS decode as
//! `5.1(side)`, and without `-ac 6` FFmpeg writes a non-standard PCE layout
//! that many players mis-handle.

use std::collections::BTreeMap;

use async_trait::async_trait;

use crate::config::StandardiseAudioConfig;
use crate::error::Result;
use crate::media::{MediaInfo, AUDIO};

use super::{PipelineContext, Plugin, PluginResult, RemuxPlan};

const LOSSLESS_CODECS: [&str; 7] = [
    "flac",
    "truehd",
    "mlp",
    "pcm_s16le",
    "pcm_s24le",
    "pcm_s32le",
    "alac",
];

const MAX_CHANNELS: [(&str, u32); 5] = [
    ("aac", 8),
    ("ac3", 6),
    ("eac3", 6),
    ("libmp3lame", 2),
    ("mp3", 2),
];

const AAC_ENCODERS: [&str; 2] = ["libfdk_aac", "aac"];

pub struct StandardiseAudioPlugin {
    config: StandardiseAudioConfig,
}

impl StandardiseAudioPlugin {
    pub fn new(config: StandardiseAudioConfig) -> Self {
        Self { config }
    }

    fn effective_cap(cap: u32, min_per_channel: u32, channels: u32) -> u32 {
        if cap == 0 {
            return 0;
        }
        let floor = if channels > 2 && min_per_channel > 0 {
            channels * min_per_channel
        } else {
            0
        };
        cap.max(floor)
    }

    fn clamp_channels(target: &str, channels: u32) -> u32 {
        if channels == 0 {
            return 0;
        }
        MAX_CHANNELS
            .iter()
            .find(|(codec, _)| *codec == target)
            .map(|(_, maximum)| channels.min(*maximum))
            .unwrap_or(channels)
    }

    async fn resolve_encoder(
        &self,
        target: &str,
        ctx: &PipelineContext,
    ) -> (String, Option<String>) {
        if target != "aac" {
            return (
                match target {
                    "mp3" => "libmp3lame".to_string(),
                    "opus" => "libopus".to_string(),
                    "vorbis" => "libvorbis".to_string(),
                    other => other.to_string(),
                },
                None,
            );
        }

        let preference = self.config.aac_encoder.trim().to_lowercase();
        if AAC_ENCODERS.contains(&preference.as_str()) {
            if preference == "aac" || ctx.ffmpeg.has_audio_encoder(&preference).await {
                return (preference, None);
            }
            return (
                "aac".to_string(),
                Some(format!(
                    "AAC encoder {preference} is not available, using the native AAC encoder."
                )),
            );
        }

        for candidate in AAC_ENCODERS {
            if ctx.ffmpeg.has_audio_encoder(candidate).await {
                if candidate != "aac" {
                    return (
                        candidate.to_string(),
                        Some(format!("Using AAC encoder {candidate}.")),
                    );
                }
                return (candidate.to_string(), None);
            }
        }
        ("aac".to_string(), None)
    }
}

#[async_trait]
impl Plugin for StandardiseAudioPlugin {
    fn key(&self) -> &'static str {
        "standardise_audio"
    }

    fn name(&self) -> &'static str {
        "Standardise Audio Codec"
    }

    fn enabled(&self) -> bool {
        self.config.enabled
    }

    async fn run(&self, media: &MediaInfo, ctx: &PipelineContext) -> Result<PluginResult> {
        let mut result = PluginResult::new(media.container());

        if !media.is_video() {
            result.note("File is not video");
            return Ok(result);
        }

        let mut target = self.config.audio_codec.trim().to_lowercase();
        if target.is_empty() {
            result.note("No audio codec configured, skipping this plugin.");
            return Ok(result);
        }
        target = match target.as_str() {
            "libmp3lame" => "mp3".to_string(),
            "libfdk_aac" => "aac".to_string(),
            "libopus" => "opus".to_string(),
            "libvorbis" => "vorbis".to_string(),
            "libflac" => "flac".to_string(),
            _ => target,
        };

        let cap = self.config.audio_bitrate_kbps;
        let min_per_channel = self.config.audio_bitrate_min_per_channel_kbps;
        let reencode_target = self.config.reencode_target_codec;

        let (encoder, encoder_note) = self.resolve_encoder(&target, ctx).await;
        if let Some(note) = encoder_note {
            result.note(note);
        }
        let encoder_label = encoder.clone();

        let mut codecs: BTreeMap<usize, String> = BTreeMap::new();
        let mut channels_out: BTreeMap<usize, u32> = BTreeMap::new();
        let mut bitrates: BTreeMap<usize, String> = BTreeMap::new();

        for stream in media.streams.iter().filter(|s| s.codec_type == AUDIO) {
            let index = stream.index;
            let codec = stream.codec();
            let source_bitrate = stream.bitrate_kbps().unwrap_or(0);
            let source_channels = stream.channels.unwrap_or(0);
            let output_channels = Self::clamp_channels(&target, source_channels);

            let broken_layout = target == "aac"
                && codec == "aac"
                && source_channels > 2
                && stream
                    .channel_layout
                    .as_deref()
                    .map(str::is_empty)
                    .unwrap_or(true);

            let needs_codec = !codec.is_empty() && (codec != target || broken_layout);
            let effective_cap = Self::effective_cap(cap, min_per_channel, source_channels);
            let needs_cap = effective_cap > 0
                && source_bitrate > 0
                && source_bitrate > effective_cap
                && (reencode_target || codec != target);

            if !needs_codec && !needs_cap {
                continue;
            }

            codecs.insert(index, encoder.clone());
            if output_channels > 0 {
                channels_out.insert(index, output_channels);
            }

            let mut target_bitrate = if effective_cap > 0 {
                effective_cap
            } else {
                source_bitrate
            };
            if target_bitrate > 0 && source_bitrate > 0 {
                target_bitrate = target_bitrate.min(source_bitrate);
            }
            if target_bitrate > 0 && !LOSSLESS_CODECS.contains(&target.as_str()) {
                bitrates.insert(index, format!("{target_bitrate}k"));
            }

            if output_channels > 0 && source_channels > 0 && output_channels < source_channels {
                result.note(format!(
                    "Audio stream {index}: downmixing {source_channels} -> {output_channels} channels for {target}."
                ));
            }

            let dims = if output_channels > 0 {
                format!("{output_channels} ch")
            } else {
                "channels unknown".to_string()
            };
            let rate = if target_bitrate > 0 {
                format!("{target_bitrate}k")
            } else {
                "source rate".to_string()
            };
            let reason = if broken_layout {
                format!(
                    "is {codec} with an invalid channel layout, re-encoding to {encoder_label} ({dims}, {rate})"
                )
            } else if needs_codec {
                format!("is {codec}, transcoding to {encoder_label} ({dims}, {rate})")
            } else {
                format!("is {source_bitrate}k {codec}, capping to {target_bitrate}k")
            };
            result.note(format!("Audio stream {index} {reason}"));
        }

        if !codecs.is_empty() {
            result.process_file = true;
            result.remux = Some(RemuxPlan {
                codecs,
                channels: channels_out,
                bitrates,
                ..Default::default()
            });
            result.requeue = true;
        } else {
            result.note(format!("All audio streams already use {target}."));
        }
        Ok(result)
    }
}
