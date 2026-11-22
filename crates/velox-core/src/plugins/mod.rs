//! Plugin engine: each step analyses a file and returns a [`PluginResult`].
//!
//! Stream-copy steps produce a [`RemuxPlan`] which the pipeline merges with its
//! neighbours into a single ffmpeg pass.  Steps that need the real file (video
//! encode, size check) set `mergeable = false`.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::config::{Config, DEFAULT_ORDER};
use crate::error::Result;
use crate::ffmpeg::FfmpegBackend;
use crate::media::MediaInfo;

mod clean_audio;
mod clean_subs;
mod file_size_check;
mod hevc_gpu;
mod reorder_all_streams;
mod reorder_streams;
mod standardise_audio;

pub use clean_audio::CleanAudioPlugin;
pub use clean_subs::CleanSubsPlugin;
pub use file_size_check::FileSizeCheckPlugin;
pub use hevc_gpu::HevcGpuPlugin;
pub use reorder_all_streams::ReorderAllStreamsPlugin;
pub use reorder_streams::ReorderStreamsPlugin;
pub use standardise_audio::StandardiseAudioPlugin;

/// A stream-copy operation expressed with absolute input stream indices.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RemuxPlan {
    /// absolute input indices of streams to remove
    #[serde(default)]
    pub drop: BTreeSet<usize>,
    /// explicit output order; `None` = keep input order
    #[serde(default)]
    pub order: Option<Vec<usize>>,
    /// per-stream codec override, e.g. `3 -> "aac"`
    #[serde(default)]
    pub codecs: BTreeMap<usize, String>,
    /// per-stream output channel count
    #[serde(default)]
    pub channels: BTreeMap<usize, u32>,
    /// per-stream bitrate, e.g. `3 -> "192k"`
    #[serde(default)]
    pub bitrates: BTreeMap<usize, String>,
    /// per-stream metadata, e.g. `3 -> [("language", "eng")]`
    #[serde(default)]
    pub metadata: BTreeMap<usize, Vec<(String, String)>>,
    /// per-stream disposition, e.g. `3 -> "default"` or `4 -> "0"`
    #[serde(default)]
    pub dispositions: BTreeMap<usize, String>,
}

impl RemuxPlan {
    pub fn merge(&mut self, other: &RemuxPlan) {
        self.drop.extend(other.drop.iter().copied());
        if other.order.is_some() {
            self.order = other.order.clone();
        }
        self.codecs.extend(other.codecs.clone());
        self.channels.extend(other.channels.clone());
        self.bitrates.extend(other.bitrates.clone());
        for (index, items) in &other.metadata {
            self.metadata
                .entry(*index)
                .or_default()
                .extend(items.clone());
        }
        self.dispositions.extend(other.dispositions.clone());
    }

    pub fn is_empty(&self) -> bool {
        self.drop.is_empty()
            && self.order.is_none()
            && self.codecs.is_empty()
            && self.channels.is_empty()
            && self.bitrates.is_empty()
            && self.metadata.is_empty()
            && self.dispositions.is_empty()
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PluginResult {
    pub process_file: bool,
    pub input_args: Vec<String>,
    pub output_args: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub remux: Option<RemuxPlan>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub container: Option<String>,
    pub requeue: bool,
    pub log: Vec<String>,
    pub error: Option<String>,
}

impl PluginResult {
    pub fn new(container: impl Into<String>) -> Self {
        Self {
            container: Some(container.into()),
            ..Default::default()
        }
    }

    pub fn note(&mut self, message: impl Into<String>) {
        self.log.push(message.into());
    }
}

pub struct PipelineContext {
    pub ffmpeg: Arc<dyn FfmpegBackend>,
    pub original_media: MediaInfo,
    pub dry_run: bool,
}

impl PipelineContext {
    pub fn new(ffmpeg: Arc<dyn FfmpegBackend>, original_media: MediaInfo, dry_run: bool) -> Self {
        Self {
            ffmpeg,
            original_media,
            dry_run,
        }
    }
}

#[async_trait]
pub trait Plugin: Send + Sync {
    fn key(&self) -> &'static str;
    fn name(&self) -> &'static str;
    fn mergeable(&self) -> bool {
        true
    }
    fn enabled(&self) -> bool;
    async fn run(&self, media: &MediaInfo, ctx: &PipelineContext) -> Result<PluginResult>;
}

pub fn plugin_display_name(key: &str) -> &'static str {
    match key {
        "reorder_streams" => "Move Video First",
        "clean_audio" => "Clean Audio Tracks",
        "clean_subs" => "Clean Subtitle Tracks",
        "standardise_audio" => "Standardise Audio Codec",
        "hevc_gpu" => "Encode Video",
        "reorder_all_streams" => "Order Streams",
        "file_size_check" => "Check File Size",
        other => Box::leak(other.to_string().into_boxed_str()),
    }
}

pub fn build_plugins(config: &Config) -> Vec<Box<dyn Plugin>> {
    let mut plugins: Vec<Box<dyn Plugin>> = Vec::new();
    let order = if config.pipeline.order.is_empty() {
        DEFAULT_ORDER
            .iter()
            .map(|value| value.to_string())
            .collect()
    } else {
        config.pipeline.order.clone()
    };

    for key in order {
        match key.as_str() {
            "reorder_streams" => plugins.push(Box::new(ReorderStreamsPlugin::new(
                config.plugins.reorder_streams.clone(),
            ))),
            "clean_audio" => plugins.push(Box::new(CleanAudioPlugin::new(
                config.plugins.clean_audio.clone(),
            ))),
            "clean_subs" => plugins.push(Box::new(CleanSubsPlugin::new(
                config.plugins.clean_subs.clone(),
            ))),
            "standardise_audio" => plugins.push(Box::new(StandardiseAudioPlugin::new(
                config.plugins.standardise_audio.clone(),
            ))),
            "hevc_gpu" => plugins.push(Box::new(HevcGpuPlugin::new(
                config.plugins.hevc_gpu.clone(),
            ))),
            "reorder_all_streams" => plugins.push(Box::new(ReorderAllStreamsPlugin::new(
                config.plugins.reorder_all_streams.clone(),
            ))),
            "file_size_check" => plugins.push(Box::new(FileSizeCheckPlugin::new(
                config.plugins.file_size_check.clone(),
            ))),
            other => tracing::warn!("Unknown plugin key {other:?} in pipeline order, skipping"),
        }
    }
    plugins
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn remux_plan_merges_in_order() {
        let mut plan = RemuxPlan {
            drop: [1].into_iter().collect(),
            metadata: BTreeMap::from([(2, vec![("language".to_string(), "eng".to_string())])]),
            ..Default::default()
        };
        let other = RemuxPlan {
            order: Some(vec![0, 2]),
            codecs: BTreeMap::from([(2, "aac".to_string())]),
            ..Default::default()
        };
        plan.merge(&other);
        assert_eq!(plan.drop, [1].into_iter().collect());
        assert_eq!(plan.order, Some(vec![0, 2]));
        assert_eq!(plan.codecs.get(&2).map(String::as_str), Some("aac"));
        assert_eq!(plan.metadata.get(&2).unwrap().len(), 1);
    }

    #[test]
    fn default_plugin_order_is_built() {
        let plugins = build_plugins(&Config::default());
        let keys: Vec<&str> = plugins.iter().map(|plugin| plugin.key()).collect();
        assert_eq!(keys, DEFAULT_ORDER.to_vec());
    }
}

#[cfg(test)]
mod behavior_tests {
    use std::sync::Arc;

    use crate::config::{
        CleanAudioConfig, CleanSubsConfig, FileSizeCheckConfig, HevcGpuConfig,
        ReorderAllStreamsConfig, ReorderStreamsConfig, StandardiseAudioConfig,
    };
    use crate::ffmpeg::tests::FakeFfmpeg;
    use crate::media::{FormatInfo, MediaInfo, ProbeData, Stream};

    use super::*;

    fn stream(index: usize, kind: &str, codec: &str) -> Stream {
        Stream {
            index,
            codec_type: kind.to_string(),
            codec_name: Some(codec.to_string()),
            ..Default::default()
        }
    }

    fn media(streams: Vec<Stream>) -> MediaInfo {
        MediaInfo::from_probe(
            "/tmp/test.mkv",
            ProbeData {
                streams,
                format: FormatInfo {
                    duration: Some("60".to_string()),
                    bit_rate: Some("5000000".to_string()),
                    ..Default::default()
                },
            },
            1_000_000,
        )
    }

    async fn run<P: Plugin>(plugin: &P, media: &MediaInfo) -> PluginResult {
        let ctx = PipelineContext::new(
            Arc::new(FakeFfmpeg::new(ProbeData::default())),
            media.clone(),
            false,
        );
        plugin.run(media, &ctx).await.expect("plugin run")
    }

    #[tokio::test]
    async fn reorder_moves_video_first() {
        let media = media(vec![stream(0, "audio", "aac"), stream(1, "video", "h264")]);
        let result = run(
            &ReorderStreamsPlugin::new(ReorderStreamsConfig::default()),
            &media,
        )
        .await;
        assert!(result.process_file);
        assert_eq!(result.remux.unwrap().order, Some(vec![1, 0]));
    }

    #[tokio::test]
    async fn reorder_is_noop_when_video_first() {
        let media = media(vec![stream(0, "video", "h264"), stream(1, "audio", "aac")]);
        let result = run(
            &ReorderStreamsPlugin::new(ReorderStreamsConfig::default()),
            &media,
        )
        .await;
        assert!(!result.process_file);
    }

    #[tokio::test]
    async fn clean_audio_removes_unwanted_language() {
        let mut german = stream(1, "audio", "aac");
        german
            .tags
            .insert("language".into(), serde_json::json!("deu"));
        let mut english = stream(0, "audio", "aac");
        english
            .tags
            .insert("language".into(), serde_json::json!("eng"));
        let media = media(vec![english, german, stream(2, "video", "h264")]);
        let result = run(&CleanAudioPlugin::new(CleanAudioConfig::default()), &media).await;
        let plan = result.remux.unwrap();
        assert_eq!(plan.drop, [1].into_iter().collect());
    }

    #[tokio::test]
    async fn clean_audio_never_removes_every_track() {
        let mut german = stream(0, "audio", "aac");
        german
            .tags
            .insert("language".into(), serde_json::json!("deu"));
        let media = media(vec![german, stream(1, "video", "h264")]);
        let config = CleanAudioConfig {
            language: "fra".into(),
            ..Default::default()
        };
        let result = run(&CleanAudioPlugin::new(config), &media).await;
        assert!(!result.process_file);
        assert!(result.remux.is_none());
    }

    #[tokio::test]
    async fn clean_audio_tags_untagged_tracks() {
        let media = media(vec![stream(0, "audio", "aac"), stream(1, "video", "h264")]);
        let config = CleanAudioConfig {
            tag_language: "eng".into(),
            ..Default::default()
        };
        let result = run(&CleanAudioPlugin::new(config), &media).await;
        let plan = result.remux.unwrap();
        assert_eq!(
            plan.metadata.get(&0).unwrap()[0],
            ("language".to_string(), "eng".to_string())
        );
    }

    #[tokio::test]
    async fn clean_audio_marks_best_default() {
        let mut stereo = stream(0, "audio", "aac");
        stereo.channels = Some(2);
        let mut surround = stream(1, "audio", "eac3");
        surround.channels = Some(6);
        surround
            .tags
            .insert("language".into(), serde_json::json!("eng"));
        let media = media(vec![stereo, surround, stream(2, "video", "h264")]);
        let config = CleanAudioConfig {
            default_audio: "best".into(),
            ..Default::default()
        };
        let result = run(&CleanAudioPlugin::new(config), &media).await;
        let plan = result.remux.unwrap();
        assert_eq!(
            plan.dispositions.get(&1).map(String::as_str),
            Some("default")
        );
        assert_eq!(plan.dispositions.get(&0).map(String::as_str), Some("0"));
    }

    #[tokio::test]
    async fn clean_audio_tags_stereo_as_2_0_and_skips_mono() {
        let mut stereo = stream(0, "audio", "aac");
        stereo.channels = Some(2);
        let mut mono = stream(1, "audio", "aac");
        mono.channels = Some(1);
        let media = media(vec![stereo, mono, stream(2, "video", "h264")]);
        let config = CleanAudioConfig {
            language: "eng,und".into(),
            tag_title: true,
            ..Default::default()
        };
        let result = run(&CleanAudioPlugin::new(config), &media).await;
        let plan = result.remux.unwrap();
        assert_eq!(
            plan.metadata.get(&0).unwrap()[0],
            ("title".to_string(), "2.0".to_string())
        );
        assert!(!plan.metadata.contains_key(&1));
    }

    #[tokio::test]
    async fn clean_subs_removes_sdh_independently() {
        let mut sdh = stream(0, "subtitle", "subrip");
        sdh.tags
            .insert("title".into(), serde_json::json!("English SDH"));
        let media = media(vec![stream(1, "video", "h264"), sdh]);
        let result = run(&CleanSubsPlugin::new(CleanSubsConfig::default()), &media).await;
        assert_eq!(result.remux.unwrap().drop, [0].into_iter().collect());
    }

    #[tokio::test]
    async fn standardise_audio_keeps_channel_count() {
        let mut audio = stream(1, "audio", "eac3");
        audio.channels = Some(6);
        audio.channel_layout = Some("5.1(side)".into());
        audio.bit_rate = Some("640000".into());
        let media = media(vec![stream(0, "video", "h264"), audio]);
        let result = run(
            &StandardiseAudioPlugin::new(StandardiseAudioConfig::default()),
            &media,
        )
        .await;
        let plan = result.remux.unwrap();
        assert_eq!(plan.codecs.get(&1).map(String::as_str), Some("libfdk_aac"));
        assert_eq!(plan.channels.get(&1).copied(), Some(6));
        assert_eq!(plan.bitrates.get(&1).map(String::as_str), Some("640k"));
    }

    #[tokio::test]
    async fn standardise_audio_repairs_broken_aac_layout() {
        let mut audio = stream(1, "audio", "aac");
        audio.channels = Some(6);
        audio.channel_layout = None;
        let media = media(vec![stream(0, "video", "h264"), audio]);
        let result = run(
            &StandardiseAudioPlugin::new(StandardiseAudioConfig::default()),
            &media,
        )
        .await;
        let plan = result.remux.unwrap();
        assert_eq!(plan.channels.get(&1).copied(), Some(6));
    }

    #[tokio::test]
    async fn standardise_audio_skips_valid_target_codec() {
        let mut audio = stream(1, "audio", "aac");
        audio.channels = Some(6);
        audio.channel_layout = Some("5.1".into());
        let media = media(vec![stream(0, "video", "h264"), audio]);
        let result = run(
            &StandardiseAudioPlugin::new(StandardiseAudioConfig::default()),
            &media,
        )
        .await;
        assert!(!result.process_file);
    }

    #[tokio::test]
    async fn standardise_audio_mp3_uses_encoder_and_downmixes() {
        let mut audio = stream(1, "audio", "ac3");
        audio.channels = Some(6);
        audio.bit_rate = Some("640000".into());
        let media = media(vec![stream(0, "video", "h264"), audio]);
        let config = StandardiseAudioConfig {
            audio_codec: "libmp3lame".into(),
            ..Default::default()
        };
        let result = run(&StandardiseAudioPlugin::new(config), &media).await;
        let plan = result.remux.unwrap();
        assert_eq!(plan.codecs.get(&1).map(String::as_str), Some("libmp3lame"));
        assert_eq!(plan.channels.get(&1).copied(), Some(2));
    }

    #[tokio::test]
    async fn hevc_skips_already_target() {
        let media = media(vec![stream(0, "video", "hevc"), stream(1, "audio", "aac")]);
        let result = run(&HevcGpuPlugin::new(HevcGpuConfig::default()), &media).await;
        assert!(!result.process_file);
    }

    #[tokio::test]
    async fn hevc_falls_back_to_cpu() {
        let media = media(vec![stream(0, "video", "h264"), stream(1, "audio", "aac")]);
        let result = run(&HevcGpuPlugin::new(HevcGpuConfig::default()), &media).await;
        assert!(result.process_file);
        assert!(result
            .output_args
            .windows(2)
            .any(|pair| pair[0] == "-c:v" && pair[1] == "libx265"));
    }

    #[tokio::test]
    async fn hevc_tier_bitrate_and_main10_flags() {
        let mut video = stream(0, "video", "h264");
        video.width = Some(1920);
        video.height = Some(1080);
        video.avg_frame_rate = Some("24/1".into());
        video.bit_rate = Some("5000000".into());
        let media = media(vec![video, stream(1, "audio", "aac")]);
        let result = run(&HevcGpuPlugin::new(HevcGpuConfig::default()), &media).await;
        assert!(result
            .output_args
            .windows(2)
            .any(|pair| pair[0] == "-b:v" && pair[1].starts_with("164")));
        assert!(result
            .output_args
            .windows(2)
            .any(|pair| pair[0] == "-profile:v" && pair[1] == "main10"));
        assert!(result
            .output_args
            .windows(2)
            .any(|pair| pair[0] == "-pix_fmt" && pair[1] == "p010le"));
    }

    #[tokio::test]
    async fn hevc_cutoff_skips_small_sources() {
        let mut video = stream(0, "video", "h264");
        video.bit_rate = Some("500000".into());
        let media = media(vec![video, stream(1, "audio", "aac")]);
        let config = HevcGpuConfig {
            bitrate_cutoff: 1000,
            ..Default::default()
        };
        let result = run(&HevcGpuPlugin::new(config), &media).await;
        assert!(!result.process_file);
    }

    #[tokio::test]
    async fn reorder_all_streams_sorts_by_type() {
        let media = media(vec![
            stream(0, "audio", "aac"),
            stream(1, "video", "h264"),
            stream(2, "subtitle", "subrip"),
        ]);
        let result = run(
            &ReorderAllStreamsPlugin::new(ReorderAllStreamsConfig::default()),
            &media,
        )
        .await;
        assert_eq!(result.remux.unwrap().order, Some(vec![1, 0, 2]));
    }

    #[tokio::test]
    async fn file_size_check_reports_oversize() {
        let media = media(vec![stream(0, "video", "hevc")]);
        let mut original = media.clone();
        original.size = 100;
        let ctx = PipelineContext::new(
            Arc::new(FakeFfmpeg::new(ProbeData::default())),
            original,
            false,
        );
        let result = FileSizeCheckPlugin::new(FileSizeCheckConfig::default())
            .run(&media, &ctx)
            .await
            .unwrap();
        assert!(result.error.is_some());
    }
}
