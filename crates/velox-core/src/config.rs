//! Typed configuration for Velox.
//!
//! The TOML format is byte-compatible with the Python version: `[general]`,
//! `[pipeline]` and one `[plugins.<key>]` section per step.  Unknown keys are
//! ignored (and reported as warnings), so a config written by a newer Velox
//! never crashes an older one.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{Result, VeloxError};

/// TOML coercion helpers matching the Python implementation: the Python config
/// loader accepts string values for booleans and numbers (`"true"`, `"3"`,
/// `"0.045"`), while serde is strict by default.
mod flex {
    use std::fmt;
    use std::marker::PhantomData;

    use serde::de::{self, Deserializer, Unexpected, Visitor};

    pub fn boolean<'de, D>(deserializer: D) -> Result<bool, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct BoolVisitor;

        impl<'de> Visitor<'de> for BoolVisitor {
            type Value = bool;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a boolean, 0/1 or a boolean-like string")
            }

            fn visit_bool<E: de::Error>(self, value: bool) -> Result<bool, E> {
                Ok(value)
            }

            fn visit_i64<E: de::Error>(self, value: i64) -> Result<bool, E> {
                match value {
                    0 => Ok(false),
                    1 => Ok(true),
                    other => Err(E::invalid_value(Unexpected::Signed(other), &self)),
                }
            }

            fn visit_u64<E: de::Error>(self, value: u64) -> Result<bool, E> {
                match value {
                    0 => Ok(false),
                    1 => Ok(true),
                    other => Err(E::invalid_value(Unexpected::Unsigned(other), &self)),
                }
            }

            fn visit_str<E: de::Error>(self, value: &str) -> Result<bool, E> {
                match value.trim().to_ascii_lowercase().as_str() {
                    "1" | "true" | "yes" | "on" => Ok(true),
                    "0" | "false" | "no" | "off" | "" => Ok(false),
                    _ => Err(E::invalid_value(Unexpected::Str(value), &self)),
                }
            }
        }

        deserializer.deserialize_any(BoolVisitor)
    }

    trait FlexNumber: Sized {
        fn from_i64(value: i64) -> Option<Self>;
        fn from_u64(value: u64) -> Option<Self>;
        fn from_f64(value: f64) -> Option<Self>;
        fn parse_str(value: &str) -> Option<Self>;
    }

    impl FlexNumber for f64 {
        fn from_i64(value: i64) -> Option<Self> {
            Some(value as f64)
        }

        fn from_u64(value: u64) -> Option<Self> {
            Some(value as f64)
        }

        fn from_f64(value: f64) -> Option<Self> {
            Some(value)
        }

        fn parse_str(value: &str) -> Option<Self> {
            value.trim().parse().ok()
        }
    }

    macro_rules! integer_number {
        ($ty:ty) => {
            impl FlexNumber for $ty {
                fn from_i64(value: i64) -> Option<Self> {
                    <$ty>::try_from(value).ok()
                }

                fn from_u64(value: u64) -> Option<Self> {
                    <$ty>::try_from(value).ok()
                }

                fn from_f64(value: f64) -> Option<Self> {
                    if value.is_finite()
                        && value.fract() == 0.0
                        && (i64::MIN as f64..=i64::MAX as f64).contains(&value)
                    {
                        <$ty>::try_from(value as i64).ok()
                    } else {
                        None
                    }
                }

                fn parse_str(value: &str) -> Option<Self> {
                    value.trim().parse().ok()
                }
            }
        };
    }

    integer_number!(u32);
    integer_number!(i32);
    integer_number!(usize);

    fn number<'de, D, T>(deserializer: D) -> Result<T, D::Error>
    where
        D: Deserializer<'de>,
        T: FlexNumber,
    {
        struct NumberVisitor<T>(PhantomData<T>);

        impl<'de, T: FlexNumber> Visitor<'de> for NumberVisitor<T> {
            type Value = T;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a number or a numeric string")
            }

            fn visit_i64<E: de::Error>(self, value: i64) -> Result<T, E> {
                T::from_i64(value).ok_or_else(|| E::invalid_value(Unexpected::Signed(value), &self))
            }

            fn visit_u64<E: de::Error>(self, value: u64) -> Result<T, E> {
                T::from_u64(value)
                    .ok_or_else(|| E::invalid_value(Unexpected::Unsigned(value), &self))
            }

            fn visit_f64<E: de::Error>(self, value: f64) -> Result<T, E> {
                T::from_f64(value).ok_or_else(|| E::invalid_value(Unexpected::Float(value), &self))
            }

            fn visit_str<E: de::Error>(self, value: &str) -> Result<T, E> {
                T::parse_str(value).ok_or_else(|| E::invalid_value(Unexpected::Str(value), &self))
            }

            fn visit_string<E: de::Error>(self, value: String) -> Result<T, E> {
                T::parse_str(&value).ok_or_else(|| E::invalid_value(Unexpected::Str(&value), &self))
            }
        }

        deserializer.deserialize_any(NumberVisitor(PhantomData))
    }

    pub fn usize_value<'de, D>(deserializer: D) -> Result<usize, D::Error>
    where
        D: Deserializer<'de>,
    {
        number::<D, usize>(deserializer)
    }

    pub fn u32_value<'de, D>(deserializer: D) -> Result<u32, D::Error>
    where
        D: Deserializer<'de>,
    {
        number::<D, u32>(deserializer)
    }

    pub fn i32_value<'de, D>(deserializer: D) -> Result<i32, D::Error>
    where
        D: Deserializer<'de>,
    {
        number::<D, i32>(deserializer)
    }

    pub fn f64_value<'de, D>(deserializer: D) -> Result<f64, D::Error>
    where
        D: Deserializer<'de>,
    {
        number::<D, f64>(deserializer)
    }
}

pub const DEFAULT_ORDER: [&str; 7] = [
    "reorder_streams",
    "clean_audio",
    "clean_subs",
    "standardise_audio",
    "hevc_gpu",
    "reorder_all_streams",
    "file_size_check",
];

pub const TDARR_IDS: [(&str, &str); 7] = [
    ("reorder_streams", "Tdarr_Plugin_lmg1_Reorder_Streams"),
    ("clean_audio", "Tdarr_Plugin_MC93_Migz3CleanAudio"),
    ("clean_subs", "Tdarr_Plugin_MC93_Migz4CleanSubs"),
    (
        "standardise_audio",
        "Tdarr_Plugin_00td_action_standardise_audio_stream_codecs",
    ),
    ("hevc_gpu", "Tdarr_Plugin_AC_HEVC_GPU"),
    (
        "reorder_all_streams",
        "Tdarr_Plugin_00td_action_re_order_all_streams_v2",
    ),
    ("file_size_check", "Tdarr_Plugin_a9he_New_file_size_check"),
];

fn default_order() -> Vec<String> {
    DEFAULT_ORDER
        .iter()
        .map(|value| (*value).to_string())
        .collect()
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct GeneralConfig {
    pub ffmpeg_path: String,
    pub ffprobe_path: String,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub keep_original: bool,
    pub output_dir: String,
    pub temp_dir: String,
    #[serde(default, deserialize_with = "flex::usize_value")]
    pub concurrency: usize,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub verify_output: bool,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub verify_full_decode: bool,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub write_report: bool,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub process_non_video: bool,
    /// Skip files that have a current successful Velox report next to them.
    #[serde(default, deserialize_with = "flex::boolean")]
    pub skip_processed: bool,
}

impl Default for GeneralConfig {
    fn default() -> Self {
        Self {
            ffmpeg_path: "ffmpeg".to_string(),
            ffprobe_path: "ffprobe".to_string(),
            keep_original: false,
            output_dir: String::new(),
            temp_dir: "source".to_string(),
            concurrency: 2,
            verify_output: true,
            verify_full_decode: false,
            write_report: true,
            process_non_video: false,
            skip_processed: true,
        }
    }
}

impl GeneralConfig {
    pub fn output_dir_path(&self) -> Option<PathBuf> {
        let value = self.output_dir.trim();
        if value.is_empty() {
            None
        } else {
            Some(PathBuf::from(shellexpand(value)))
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct PipelineConfig {
    pub order: Vec<String>,
}

impl Default for PipelineConfig {
    fn default() -> Self {
        Self {
            order: default_order(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct ReorderStreamsConfig {
    #[serde(default, deserialize_with = "flex::boolean")]
    pub enabled: bool,
}

impl Default for ReorderStreamsConfig {
    fn default() -> Self {
        Self { enabled: true }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct CleanAudioConfig {
    #[serde(default, deserialize_with = "flex::boolean")]
    pub enabled: bool,
    pub language: String,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub commentary: bool,
    pub tag_language: String,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub tag_title: bool,
    pub default_audio: String,
}

impl Default for CleanAudioConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            language: "eng,und".to_string(),
            commentary: false,
            tag_language: String::new(),
            tag_title: false,
            default_audio: String::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct CleanSubsConfig {
    #[serde(default, deserialize_with = "flex::boolean")]
    pub enabled: bool,
    pub language: String,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub commentary: bool,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub sdh: bool,
    pub tag_language: String,
}

impl Default for CleanSubsConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            language: "eng".to_string(),
            commentary: false,
            sdh: true,
            tag_language: String::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct StandardiseAudioConfig {
    #[serde(default, deserialize_with = "flex::boolean")]
    pub enabled: bool,
    pub audio_codec: String,
    #[serde(default, deserialize_with = "flex::u32_value")]
    pub audio_bitrate_kbps: u32,
    #[serde(default, deserialize_with = "flex::u32_value")]
    pub audio_bitrate_min_per_channel_kbps: u32,
    pub aac_encoder: String,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub reencode_target_codec: bool,
}

impl Default for StandardiseAudioConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            audio_codec: "aac".to_string(),
            audio_bitrate_kbps: 0,
            audio_bitrate_min_per_channel_kbps: 128,
            aac_encoder: "auto".to_string(),
            reencode_target_codec: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct HevcGpuConfig {
    #[serde(default, deserialize_with = "flex::boolean")]
    pub enabled: bool,
    pub target_codec: String,
    pub encoder: String,
    pub bitrate_mode: String,
    #[serde(default, deserialize_with = "flex::f64_value")]
    pub bits_per_pixel: f64,
    #[serde(default, deserialize_with = "flex::f64_value")]
    pub target_size_mb: f64,
    #[serde(default, deserialize_with = "flex::f64_value")]
    pub size_overhead_percent: f64,
    #[serde(default, deserialize_with = "flex::u32_value")]
    pub fallback_audio_kbps: u32,
    #[serde(default, deserialize_with = "flex::i32_value")]
    pub quality: i32,
    #[serde(default, deserialize_with = "flex::u32_value")]
    pub min_video_kbps: u32,
    #[serde(default, deserialize_with = "flex::u32_value")]
    pub max_video_kbps: u32,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub hardware_decode: bool,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub try_use_gpu: bool,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub allow_cpu_fallback: bool,
    pub cpu_preset: String,
    pub container: String,
    #[serde(default, deserialize_with = "flex::u32_value")]
    pub bitrate_cutoff: u32,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub enable_10bit: bool,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub bframes_enabled: bool,
    #[serde(default, deserialize_with = "flex::u32_value")]
    pub bframes_value: u32,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub spatial_aq: bool,
    #[serde(default, deserialize_with = "flex::boolean")]
    pub force_conform: bool,
    pub exclude_gpus: String,
    pub allowed_codecs: String,
}

impl Default for HevcGpuConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            target_codec: "hevc".to_string(),
            encoder: "hevc_videotoolbox".to_string(),
            bitrate_mode: "quality".to_string(),
            bits_per_pixel: 0.033,
            target_size_mb: 1500.0,
            size_overhead_percent: 2.0,
            fallback_audio_kbps: 256,
            quality: 50,
            min_video_kbps: 300,
            max_video_kbps: 40_000,
            hardware_decode: true,
            try_use_gpu: true,
            allow_cpu_fallback: true,
            cpu_preset: "medium".to_string(),
            container: "mkv".to_string(),
            bitrate_cutoff: 0,
            enable_10bit: true,
            bframes_enabled: false,
            bframes_value: 5,
            spatial_aq: true,
            force_conform: false,
            exclude_gpus: String::new(),
            allowed_codecs: "vp9,av1".to_string(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct ReorderAllStreamsConfig {
    #[serde(default, deserialize_with = "flex::boolean")]
    pub enabled: bool,
    pub process_order: String,
    pub languages: String,
    pub channels: String,
    pub codecs: String,
    pub stream_types: String,
}

impl Default for ReorderAllStreamsConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            process_order: "codecs,channels,languages,streamTypes".to_string(),
            languages: String::new(),
            channels: "7.1,5.1,2,1".to_string(),
            codecs: String::new(),
            stream_types: "video,audio,subtitle".to_string(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct FileSizeCheckConfig {
    #[serde(default, deserialize_with = "flex::boolean")]
    pub enabled: bool,
    #[serde(default, deserialize_with = "flex::f64_value")]
    pub upper_bound: f64,
    #[serde(default, deserialize_with = "flex::f64_value")]
    pub lower_bound: f64,
    #[serde(default, deserialize_with = "flex::f64_value")]
    pub min_size_mb: f64,
    #[serde(default, deserialize_with = "flex::f64_value")]
    pub max_size_mb: f64,
}

impl Default for FileSizeCheckConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            upper_bound: 110.0,
            lower_bound: 0.0,
            min_size_mb: 0.0,
            max_size_mb: 0.0,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct PluginsConfig {
    pub reorder_streams: ReorderStreamsConfig,
    pub clean_audio: CleanAudioConfig,
    pub clean_subs: CleanSubsConfig,
    pub standardise_audio: StandardiseAudioConfig,
    pub hevc_gpu: HevcGpuConfig,
    pub reorder_all_streams: ReorderAllStreamsConfig,
    pub file_size_check: FileSizeCheckConfig,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub general: GeneralConfig,
    pub pipeline: PipelineConfig,
    pub plugins: PluginsConfig,
}

impl Config {
    /// Load a config from a TOML file, recording ignored keys as warnings.
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let text = fs::read_to_string(path).map_err(|error| {
            VeloxError::config(format!("Could not read config {}: {error}", path.display()))
        })?;
        Self::from_toml(&text).map_err(|error| {
            VeloxError::config(format!("Invalid config file {}: {error}", path.display()))
        })
    }

    pub fn from_toml(text: &str) -> Result<Self> {
        let deserializer = toml::Deserializer::new(text);
        let mut ignored = Vec::new();
        let mut config: Config = serde_ignored::deserialize(deserializer, |path| {
            ignored.push(path.to_string());
        })
        .map_err(|error| VeloxError::config(error.to_string()))?;

        for key in ignored {
            tracing::warn!("Ignoring unknown config key {key}");
        }

        config.normalize();
        Ok(config)
    }

    pub fn to_toml(&self) -> Result<String> {
        toml::to_string_pretty(self).map_err(|error| VeloxError::config(error.to_string()))
    }

    pub fn save(&self, path: impl AsRef<Path>) -> Result<()> {
        let path = path.as_ref();
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }
        let text = self.to_toml()?;
        let tmp = path.with_extension("toml.tmp");
        fs::write(&tmp, text)?;
        fs::rename(&tmp, path)?;
        Ok(())
    }

    fn normalize(&mut self) {
        if self.general.concurrency == 0 {
            self.general.concurrency = 1;
        }
        if self.general.temp_dir.trim().is_empty() {
            self.general.temp_dir = "source".to_string();
        }
        if self.pipeline.order.is_empty() {
            self.pipeline.order = default_order();
        }
    }

    /// Set the `enabled` flag for a plugin key, if it exists.
    pub fn set_plugin_enabled(&mut self, key: &str, enabled: bool) {
        match key {
            "reorder_streams" => self.plugins.reorder_streams.enabled = enabled,
            "clean_audio" => self.plugins.clean_audio.enabled = enabled,
            "clean_subs" => self.plugins.clean_subs.enabled = enabled,
            "standardise_audio" => self.plugins.standardise_audio.enabled = enabled,
            "hevc_gpu" => self.plugins.hevc_gpu.enabled = enabled,
            "reorder_all_streams" => self.plugins.reorder_all_streams.enabled = enabled,
            "file_size_check" => self.plugins.file_size_check.enabled = enabled,
            _ => {}
        }
    }

    pub fn plugin_enabled(&self, key: &str) -> Option<bool> {
        Some(match key {
            "reorder_streams" => self.plugins.reorder_streams.enabled,
            "clean_audio" => self.plugins.clean_audio.enabled,
            "clean_subs" => self.plugins.clean_subs.enabled,
            "standardise_audio" => self.plugins.standardise_audio.enabled,
            "hevc_gpu" => self.plugins.hevc_gpu.enabled,
            "reorder_all_streams" => self.plugins.reorder_all_streams.enabled,
            "file_size_check" => self.plugins.file_size_check.enabled,
            _ => return None,
        })
    }
}

/// Where the GUI stores settings, matching the Python version.
pub fn user_config_path() -> PathBuf {
    if cfg!(target_os = "macos") {
        home_dir()
            .join("Library")
            .join("Application Support")
            .join("Velox")
            .join("config.toml")
    } else {
        let base = env::var_os("XDG_CONFIG_HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".config"));
        base.join("velox").join("config.toml")
    }
}

pub fn load_user_config() -> Config {
    let path = user_config_path();
    if path.exists() {
        match Config::load(&path) {
            Ok(config) => config,
            Err(error) => {
                tracing::error!("Could not load settings: {error}");
                Config::default()
            }
        }
    } else {
        Config::default()
    }
}

fn home_dir() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn shellexpand(value: &str) -> String {
    if let Some(rest) = value.strip_prefix("~/") {
        if let Some(home) = env::var_os("HOME") {
            return PathBuf::from(home)
                .join(rest)
                .to_string_lossy()
                .into_owned();
        }
    }
    value.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_python() {
        let config = Config::default();
        assert_eq!(config.general.ffmpeg_path, "ffmpeg");
        assert!(!config.general.keep_original);
        assert_eq!(config.general.concurrency, 2);
        assert!(config.general.verify_output);
        assert!(config.general.write_report);
        assert!(config.general.skip_processed);
        assert_eq!(config.plugins.hevc_gpu.encoder, "hevc_videotoolbox");
        assert_eq!(config.plugins.hevc_gpu.bitrate_mode, "quality");
        assert_eq!(config.plugins.hevc_gpu.quality, 50);
        assert!(config.plugins.hevc_gpu.enable_10bit);
        assert_eq!(config.plugins.clean_audio.language, "eng,und");
        assert_eq!(config.plugins.clean_subs.language, "eng");
        assert!(config.plugins.clean_subs.sdh);
        assert_eq!(config.pipeline.order.len(), 7);
    }

    #[test]
    fn example_config_loads() {
        let text = include_str!("../../../config.example.toml");
        let config = Config::from_toml(text).expect("example config must load");
        assert_eq!(config.plugins.hevc_gpu.encoder, "hevc_videotoolbox");
        assert_eq!(config.general.concurrency, 2);
        assert!(config.plugins.standardise_audio.enabled);
        assert_eq!(config.plugins.file_size_check.upper_bound, 110.0);
    }

    #[test]
    fn default_config_round_trips() {
        let config = Config::default();
        let text = config.to_toml().unwrap();
        let parsed = Config::from_toml(&text).unwrap();
        assert_eq!(config, parsed);
    }

    #[test]
    fn unknown_keys_are_ignored() {
        let config = Config::from_toml(
            r#"
[general]
ffmpeg_path = "/opt/ffmpeg"
unknown_option = 1

[plugins.clean_audio]
language = "deu"
mystery = "ignored"
"#,
        )
        .unwrap();
        assert_eq!(config.general.ffmpeg_path, "/opt/ffmpeg");
        assert_eq!(config.plugins.clean_audio.language, "deu");
    }

    #[test]
    fn string_values_are_coerced() {
        let config = Config::from_toml(
            r#"
[general]
concurrency = "3"
verify_output = "no"

[plugins.hevc_gpu]
bits_per_pixel = "0.045"
enable_10bit = "true"
bframes_value = "7"
quality = "55"
target_size_mb = "1500"
"#,
        )
        .unwrap();
        assert_eq!(config.general.concurrency, 3);
        assert!(!config.general.verify_output);
        assert!(!config.general.keep_original);
        assert_eq!(config.plugins.hevc_gpu.bits_per_pixel, 0.045);
        assert!(config.plugins.hevc_gpu.enable_10bit);
        assert_eq!(config.plugins.hevc_gpu.bframes_value, 7);
        assert_eq!(config.plugins.hevc_gpu.quality, 55);
        assert_eq!(config.plugins.hevc_gpu.target_size_mb, 1500.0);
    }

    #[test]
    fn plugin_selection() {
        let mut config = Config::default();
        config.set_plugin_enabled("hevc_gpu", false);
        assert_eq!(config.plugin_enabled("hevc_gpu"), Some(false));
        assert_eq!(config.plugin_enabled("nope"), None);
    }
}
