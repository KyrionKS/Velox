//! Encode Video - hardware video transcode.
//!
//! Port of the Python `hevc_gpu` plugin, including the Apple Silicon tuned
//! constant-quality mode, tier/size bitrate modes, encoder probing, hardware
//! decode, Main10, spatial AQ and CPU fallback.

use async_trait::async_trait;
use tokio::process::Command;

use crate::config::HevcGpuConfig;
use crate::error::Result;
use crate::media::{MediaInfo, AUDIO, IMAGE_CODECS};
use crate::plugins::PipelineContext;
use crate::plugins::{Plugin, PluginResult};

#[derive(Clone, Debug)]
struct EncoderChoice {
    encoder: String,
    input_args: Vec<String>,
    output_args: Vec<String>,
    video_filter: Option<String>,
}

const GPU_ENCODER_ORDER: [&str; 9] = [
    "hevc_nvenc",
    "hevc_amf",
    "hevc_qsv",
    "hevc_videotoolbox",
    "h264_nvenc",
    "h264_amf",
    "h264_qsv",
    "h264_videotoolbox",
    "hevc_vaapi",
];

const NVENC_ENCODERS: [&str; 2] = ["hevc_nvenc", "h264_nvenc"];
const VIDEOTOOLBOX_ENCODERS: [&str; 2] = ["hevc_videotoolbox", "h264_videotoolbox"];
const BITRATE_MODES: [&str; 3] = ["tier", "size", "quality"];

fn gpu_spec(name: &str) -> Option<EncoderChoice> {
    let (input_args, video_filter) = if name == "hevc_vaapi" {
        (
            vec![
                "-hwaccel".to_string(),
                "vaapi".to_string(),
                "-hwaccel_device".to_string(),
                "/dev/dri/renderD128".to_string(),
                "-hwaccel_output_format".to_string(),
                "vaapi".to_string(),
            ],
            Some("format=nv12,hwupload".to_string()),
        )
    } else {
        (Vec::new(), None)
    };
    Some(EncoderChoice {
        encoder: name.to_string(),
        input_args,
        output_args: Vec::new(),
        video_filter,
    })
}

fn encoder_codec(encoder: &str) -> Option<&'static str> {
    Some(match encoder {
        "hevc_nvenc" | "hevc_amf" | "hevc_qsv" | "hevc_videotoolbox" | "hevc_vaapi" | "libx265" => {
            "hevc"
        }
        "h264_nvenc" | "h264_amf" | "h264_qsv" | "h264_videotoolbox" | "libx264" => "h264",
        _ => return None,
    })
}

fn conform_codecs(container: &str) -> &'static [&'static str] {
    match container {
        "mkv" => &["eia_608", "timed_id3", "dvb_teletext", "bin_data"],
        "mp4" => &["hdmv_pgs_subtitle", "eia_608", "subrip", "timed_id3"],
        _ => &[],
    }
}

fn is_10bit(stream: Option<&crate::media::Stream>) -> bool {
    let Some(stream) = stream else {
        return false;
    };
    let profile = stream.profile.clone().unwrap_or_default().to_lowercase();
    profile == "high 10"
        || profile == "main 10"
        || stream.bits_per_raw_sample.as_deref() == Some("10")
        || stream.pix_fmt.as_deref() == Some("yuv420p10le")
}

pub struct HevcGpuPlugin {
    config: HevcGpuConfig,
}

impl HevcGpuPlugin {
    pub fn new(config: HevcGpuConfig) -> Self {
        Self { config }
    }

    fn constant_quality_supported(&self, encoder: &str, result: &mut PluginResult) -> bool {
        if !VIDEOTOOLBOX_ENCODERS.contains(&encoder) {
            result.note(format!(
                "Constant quality is only supported by VideoToolbox; {encoder} uses tier mode instead."
            ));
            return false;
        }
        if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
            return true;
        }
        result.note(
            "Constant quality (-q:v) on VideoToolbox requires Apple Silicon, using tier mode instead.",
        );
        false
    }

    fn clamp(&self, kbps: u32) -> u32 {
        kbps.max(self.config.min_video_kbps)
            .min(self.config.max_video_kbps)
    }

    fn video_dimensions(&self, media: &MediaInfo) -> (u32, u32) {
        if let Some(stream) = media.video_stream() {
            let width = stream.width.unwrap_or(0);
            let height = stream.height.unwrap_or(0);
            if width > 0 && height > 0 {
                return (width, height);
            }
        }
        (1920, 1080)
    }

    fn fps(&self, media: &MediaInfo) -> f64 {
        if let Some(stream) = media.video_stream() {
            for raw in [&stream.avg_frame_rate, &stream.r_frame_rate]
                .into_iter()
                .flatten()
            {
                if raw.is_empty() || raw == "0/0" || raw == "0" {
                    continue;
                }
                let mut parts = raw.split('/');
                let numerator = parts.next().and_then(|value| value.parse::<f64>().ok());
                let denominator = parts
                    .next()
                    .and_then(|value| value.parse::<f64>().ok())
                    .unwrap_or(1.0);
                if let Some(numerator) = numerator {
                    if denominator != 0.0 {
                        let fps = numerator / denominator;
                        if fps > 0.0 {
                            return fps;
                        }
                    }
                }
            }
        }
        24.0
    }

    fn tier_bitrate(&self, media: &MediaInfo, result: &mut PluginResult) -> u32 {
        let (width, height) = self.video_dimensions(media);
        let fps = self.fps(media);
        let bpp = self.config.bits_per_pixel;
        let video_kbps =
            (bpp * width as f64 * height as f64 * fps / 1000.0).round_ties_even() as u32;
        let clamped = self.clamp(video_kbps);
        result.note(format!(
            "Tier mode: {bpp} bpp x {width}x{height}@{fps}fps -> {video_kbps}k{}",
            if clamped != video_kbps {
                format!(" (clamped to {clamped}k)")
            } else {
                String::new()
            }
        ));
        clamped
    }

    fn size_target_bitrate(
        &self,
        media: &MediaInfo,
        duration: f64,
        result: &mut PluginResult,
    ) -> u32 {
        let target_mb = self.config.target_size_mb;
        let overhead = self.config.size_overhead_percent;
        let total_kbps = (target_mb * 1024.0 * 1024.0 * 8.0) / duration / 1000.0;
        let usable_kbps = total_kbps * (1.0 - overhead / 100.0);
        let audio_kbps = self.audio_budget_kbps(media);
        let video_kbps = (usable_kbps - audio_kbps as f64).round_ties_even() as u32;
        let clamped = self.clamp(video_kbps);
        result.note(format!(
            "Size mode: {target_mb:.0} MiB over {duration:.0}s -> {total_kbps:.0}k total, audio budget {audio_kbps}k -> video {video_kbps}k{}",
            if clamped != video_kbps {
                format!(" (clamped to {clamped}k)")
            } else {
                String::new()
            }
        ));
        clamped
    }

    fn quality_ceiling(&self, media: &MediaInfo) -> u32 {
        let (width, height) = self.video_dimensions(media);
        let fps = self.fps(media);
        let bpp = self.config.bits_per_pixel;
        self.clamp(
            (bpp * 2.0 * width as f64 * height as f64 * fps / 1000.0).round_ties_even() as u32,
        )
    }

    fn audio_budget_kbps(&self, media: &MediaInfo) -> u32 {
        media
            .streams_of(AUDIO)
            .iter()
            .map(|stream| {
                stream
                    .bitrate_kbps()
                    .unwrap_or(self.config.fallback_audio_kbps)
            })
            .sum()
    }

    fn current_bitrate(&self, media: &MediaInfo) -> (Option<u32>, &'static str) {
        if let Some(video) = media.video_stream() {
            if let Some(bitrate) = video.bitrate_kbps() {
                if bitrate > 0 {
                    return (Some(bitrate), "ffprobe stream tags");
                }
            }
        }
        if let Some(bitrate) = media.bitrate_kbps() {
            return (Some(bitrate), "container bitrate");
        }
        (None, "none")
    }

    async fn select_encoder(
        &self,
        target_codec: &str,
        ctx: &PipelineContext,
        result: &mut PluginResult,
    ) -> Option<EncoderChoice> {
        let configured = self.config.encoder.trim();

        if !configured.is_empty() && !configured.eq_ignore_ascii_case("auto") {
            let mut configured_mismatch = false;
            if let Some(selected) = self.probe_encoder(configured, ctx, result).await {
                if let Some(produced) = encoder_codec(&selected.encoder) {
                    if produced != target_codec {
                        result.note(format!(
                            "Configured encoder {} produces {produced}, not the target codec {target_codec}; falling back to automatic detection.",
                            selected.encoder
                        ));
                        configured_mismatch = true;
                    } else {
                        result.note(format!("Using configured encoder: {}", selected.encoder));
                        return Some(selected);
                    }
                } else {
                    result.note(format!("Using configured encoder: {}", selected.encoder));
                    return Some(selected);
                }
            }
            if !configured_mismatch {
                result.note(format!(
                    "Configured encoder {configured} is not available, falling back to automatic detection."
                ));
            }
        }

        if self.config.try_use_gpu {
            for candidate in GPU_ENCODER_ORDER {
                if !candidate.contains(target_codec) {
                    continue;
                }
                if let Some(selected) = self.probe_encoder(candidate, ctx, result).await {
                    result.note(format!("Using hardware encoder: {}", selected.encoder));
                    return Some(selected);
                }
            }
            result.note("No hardware encoder available.");
        }

        if self.config.allow_cpu_fallback {
            let cpu = match target_codec {
                "hevc" => "libx265",
                "h264" => "libx264",
                _ => {
                    result.error = Some(format!(
                        "No CPU encoder is available for target codec {target_codec}."
                    ));
                    return None;
                }
            };
            result.note(format!("Falling back to CPU encoder: {cpu}"));
            return Some(EncoderChoice {
                encoder: cpu.to_string(),
                input_args: Vec::new(),
                output_args: Vec::new(),
                video_filter: None,
            });
        }
        None
    }

    async fn probe_encoder(
        &self,
        name: &str,
        ctx: &PipelineContext,
        result: &mut PluginResult,
    ) -> Option<EncoderChoice> {
        let mut selected = gpu_spec(name)?;
        if !ctx
            .ffmpeg
            .has_encoder(name, &selected.input_args, selected.video_filter.as_deref())
            .await
        {
            return None;
        }
        if NVENC_ENCODERS.contains(&name) {
            self.apply_best_nvenc(&mut selected, result).await;
        }
        Some(selected)
    }

    async fn apply_best_nvenc(&self, encoder: &mut EncoderChoice, result: &mut PluginResult) {
        let exclude: Vec<usize> = self
            .config
            .exclude_gpus
            .split(',')
            .filter_map(|value| value.trim().parse::<usize>().ok())
            .collect();

        let names_output = Command::new("nvidia-smi")
            .args(["--query-gpu=name", "--format=csv,noheader"])
            .output()
            .await;
        let Ok(names_output) = names_output else {
            return;
        };
        if !names_output.status.success() {
            return;
        }
        let names: Vec<String> = String::from_utf8_lossy(&names_output.stdout)
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| line.trim().to_string())
            .collect();

        let mut best_index: Option<usize> = None;
        let mut best_util = u64::MAX;
        for (index, name) in names.iter().enumerate() {
            if exclude.contains(&index) {
                result.note(format!(
                    "GPU {index}: {name} is in the exclusion list, skipping."
                ));
                continue;
            }
            let util_output = Command::new("nvidia-smi")
                .args([
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                    "-i",
                    &index.to_string(),
                ])
                .output()
                .await;
            let Ok(util_output) = util_output else {
                continue;
            };
            let Ok(util) = String::from_utf8_lossy(&util_output.stdout)
                .trim()
                .parse::<u64>()
            else {
                continue;
            };
            result.note(format!("GPU {index}: utilization {util}%"));
            if util < best_util {
                best_index = Some(index);
                best_util = util;
            }
        }

        if let Some(index) = best_index {
            encoder.input_args = vec!["-hwaccel_device".to_string(), index.to_string()];
            encoder.output_args = vec!["-gpu".to_string(), index.to_string()];
        }
    }

    fn video_encode_args(
        &self,
        encoder: &str,
        mode: &str,
        target: u32,
        minimum: u32,
        maximum: u32,
        ceiling: u32,
    ) -> Vec<String> {
        let mut args = if mode == "quality" {
            self.quality_mode_args(encoder, ceiling)
        } else {
            self.bitrate_mode_args(encoder, target, minimum, maximum)
        };

        if encoder.contains("nvenc") {
            args.extend([
                "-spatial_aq:v".to_string(),
                "1".to_string(),
                "-rc-lookahead:v".to_string(),
                "32".to_string(),
            ]);
        } else if encoder.contains("videotoolbox") && self.config.spatial_aq {
            args.extend(["-spatial_aq".to_string(), "1".to_string()]);
        }
        args
    }

    fn bitrate_mode_args(
        &self,
        encoder: &str,
        target: u32,
        minimum: u32,
        maximum: u32,
    ) -> Vec<String> {
        let target = target.max(1);
        if encoder.contains("videotoolbox") {
            return vec![
                "-b:v".to_string(),
                format!("{target}k"),
                "-maxrate".to_string(),
                format!("{maximum}k"),
            ];
        }
        if encoder.contains("qsv") || encoder.contains("vaapi") {
            return vec![
                "-b:v".to_string(),
                format!("{target}k"),
                "-maxrate".to_string(),
                format!("{maximum}k"),
                "-bufsize".to_string(),
                format!("{}k", target * 2),
            ];
        }
        if encoder.contains("amf") {
            return vec![
                "-b:v".to_string(),
                format!("{target}k"),
                "-minrate".to_string(),
                format!("{minimum}k"),
                "-maxrate".to_string(),
                format!("{maximum}k"),
                "-bufsize".to_string(),
                format!("{}k", target * 2),
            ];
        }
        if encoder.contains("nvenc") {
            return vec![
                "-cq:v".to_string(),
                "19".to_string(),
                "-b:v".to_string(),
                format!("{target}k"),
                "-minrate".to_string(),
                format!("{minimum}k"),
                "-maxrate".to_string(),
                format!("{maximum}k"),
                "-bufsize".to_string(),
                format!("{}k", target * 2),
            ];
        }
        vec![
            "-preset".to_string(),
            self.config.cpu_preset.clone(),
            "-b:v".to_string(),
            format!("{target}k"),
            "-maxrate".to_string(),
            format!("{maximum}k"),
            "-bufsize".to_string(),
            format!("{}k", target * 2),
        ]
    }

    fn quality_mode_args(&self, encoder: &str, ceiling: u32) -> Vec<String> {
        let quality = self.config.quality.to_string();
        let mut maxrate = if ceiling > 0 {
            vec!["-maxrate".to_string(), format!("{ceiling}k")]
        } else {
            vec![]
        };
        let bufsize = if ceiling > 0 {
            vec!["-bufsize".to_string(), format!("{}k", ceiling as u64 * 2)]
        } else {
            vec![]
        };

        if encoder.contains("videotoolbox") {
            let mut args = vec!["-q:v".to_string(), quality];
            args.append(&mut maxrate);
            return args;
        }
        if encoder.contains("nvenc") {
            let mut args = vec!["-cq:v".to_string(), quality];
            args.append(&mut maxrate);
            args.extend(bufsize);
            return args;
        }
        if encoder.contains("qsv") {
            let mut args = vec!["-global_quality".to_string(), quality];
            args.append(&mut maxrate);
            args.extend(bufsize);
            return args;
        }
        if encoder.contains("vaapi") {
            let mut args = vec!["-qp".to_string(), quality];
            args.append(&mut maxrate);
            args.extend(bufsize);
            return args;
        }
        if encoder.contains("amf") {
            let mut args = vec![
                "-rc".to_string(),
                "cqp".to_string(),
                "-qp_i".to_string(),
                quality.clone(),
                "-qp_p".to_string(),
                quality,
            ];
            args.append(&mut maxrate);
            args.extend(bufsize);
            return args;
        }
        let mut args = vec![
            "-preset".to_string(),
            self.config.cpu_preset.clone(),
            "-crf".to_string(),
            quality,
        ];
        args.append(&mut maxrate);
        args.extend(bufsize);
        args
    }
}

#[async_trait]
impl Plugin for HevcGpuPlugin {
    fn key(&self) -> &'static str {
        "hevc_gpu"
    }

    fn name(&self) -> &'static str {
        "Encode Video"
    }

    fn mergeable(&self) -> bool {
        false
    }

    fn enabled(&self) -> bool {
        self.config.enabled
    }

    async fn run(&self, media: &MediaInfo, ctx: &PipelineContext) -> Result<PluginResult> {
        let target_codec = self.config.target_codec.trim().to_lowercase();
        let configured_container = self.config.container.trim().to_lowercase();
        let container = if configured_container == "original" {
            media.container()
        } else {
            configured_container
        };
        let mut result = PluginResult::new(container.clone());

        let Some(encoder) = self.select_encoder(&target_codec, ctx, &mut result).await else {
            if result.error.is_none() {
                result.error = Some(
                    "No usable hardware encoder was found and CPU fallback is disabled. Enable CPU fallback or install a GPU/videotoolbox-capable ffmpeg."
                        .to_string(),
                );
            }
            return Ok(result);
        };

        if !media.is_video() {
            result.note("File is not a video.");
            return Ok(result);
        }

        let mut mode = self.config.bitrate_mode.trim().to_lowercase();
        if !BITRATE_MODES.contains(&mode.as_str()) {
            result.note(format!(
                "Unknown bitrate_mode {mode:?}, falling back to quality."
            ));
            mode = "quality".to_string();
        }
        if mode == "quality" && !self.constant_quality_supported(&encoder.encoder, &mut result) {
            mode = "tier".to_string();
        }

        let duration = media.duration();
        let (current_bitrate, source) = self.current_bitrate(media);
        if let Some(bitrate) = current_bitrate {
            result.note(format!("Current video bitrate ~{bitrate}kbps ({source})"));
        }

        let mut target_bitrate: Option<u32> = None;
        let mut minimum_bitrate = 0u32;
        let mut maximum_bitrate = 0u32;
        let cutoff = self.config.bitrate_cutoff;

        if mode == "quality" {
            let quality = self.config.quality;
            if cutoff > 0
                && current_bitrate
                    .map(|bitrate| bitrate < cutoff)
                    .unwrap_or(false)
            {
                result.note(format!(
                    "Source video bitrate {}k is below the cutoff of {cutoff}k. Skipping this plugin.",
                    current_bitrate.unwrap_or(0)
                ));
                return Ok(result);
            }
            result.note(format!(
                "Quality mode: constant quality {quality} (VideoToolbox -q:v)"
            ));
        } else {
            let computed = if mode == "size" {
                if duration <= 0.0 {
                    result.note("Could not determine duration for the size target. Skipping.");
                    return Ok(result);
                }
                self.size_target_bitrate(media, duration, &mut result)
            } else {
                self.tier_bitrate(media, &mut result)
            };

            let computed = if let Some(current) = current_bitrate {
                if computed > current {
                    result.note(format!(
                        "Computed target {computed}k is above the source {current}k, capping to the source bitrate."
                    ));
                    current
                } else {
                    computed
                }
            } else {
                computed
            };

            if computed == 0 {
                result.note("Target bitrate could not be calculated. Skipping this plugin.");
                return Ok(result);
            }
            if cutoff > 0 && cutoff >= computed {
                result.note(format!(
                    "File target bitrate {computed}k is below the set cutoff of {cutoff}k. Skipping this plugin."
                ));
                return Ok(result);
            }

            target_bitrate = Some(computed);
            minimum_bitrate = (computed as f64 * 0.75).round_ties_even() as u32;
            maximum_bitrate = (computed as f64 * 1.25).round_ties_even() as u32;
            result.note("Bitrate settings:");
            result.note(format!("  target  = {computed}kbps"));
            result.note(format!("  minimum = {minimum_bitrate}kbps"));
            result.note(format!("  maximum = {maximum_bitrate}kbps"));
        }

        let mut remux_extra: Vec<String> = Vec::new();
        let mut encode_extra: Vec<String> = Vec::new();
        let force_conform = self.config.force_conform;

        if force_conform {
            let spec = conform_codecs(&container);
            if !spec.is_empty() {
                if container == "mkv" {
                    remux_extra.extend(["-map".to_string(), "-0:d".to_string()]);
                }
                for stream in &media.streams {
                    if spec.contains(&stream.codec().as_str()) {
                        remux_extra.push("-map".to_string());
                        remux_extra.push(format!("-0:{}", stream.index));
                    }
                }
            }
        }

        let tenbit = self.config.enable_10bit || is_10bit(media.video_stream());
        if tenbit && target_codec == "hevc" {
            encode_extra.extend(["-profile:v".to_string(), "main10".to_string()]);
            if encoder.encoder != "hevc_vaapi" {
                encode_extra.extend(["-pix_fmt".to_string(), "p010le".to_string()]);
            }
            result.note(format!(
                "10-bit encode enabled (Main10, {}).",
                if self.config.enable_10bit {
                    "user enabled in config"
                } else {
                    "source is 10-bit"
                }
            ));
        }

        if self.config.bframes_enabled && NVENC_ENCODERS.contains(&encoder.encoder.as_str()) {
            encode_extra.extend(["-bf".to_string(), self.config.bframes_value.to_string()]);
        }

        let allowed: Vec<String> = self
            .config
            .allowed_codecs
            .split(',')
            .map(|value| value.trim().to_lowercase())
            .filter(|value| !value.is_empty())
            .collect();

        let mut sub_idx = 0usize;
        let mut already_target = false;
        for stream in &media.streams {
            let ctype = stream.codec_type.as_str();
            let cname = stream.codec();

            if ctype == "subtitle" {
                if container == "mkv" && cname == "mov_text" {
                    remux_extra.extend([format!("-c:s:{sub_idx}"), "srt".to_string()]);
                    result.note("Converting mov_text subtitles to srt (mkv target).");
                } else if container == "mp4" && cname == "subrip" {
                    remux_extra.extend([format!("-c:s:{sub_idx}"), "mov_text".to_string()]);
                    result.note("Converting subrip subtitles to mov_text (mp4 target).");
                }
                sub_idx += 1;
                continue;
            }

            if ctype == "data" {
                if container == "mkv" {
                    remux_extra.push("-map".to_string());
                    remux_extra.push(format!("-0:{}", stream.index));
                    result.note(format!(
                        "Removing data stream {} (mkv does not support it).",
                        stream.index
                    ));
                }
                continue;
            }

            if ctype != "video" {
                continue;
            }

            if IMAGE_CODECS.contains(&cname.as_str()) {
                remux_extra.push("-map".to_string());
                remux_extra.push(format!("-0:{}", stream.index));
                result.note(format!(
                    "Removing {cname}:{} as it interferes with encoding.",
                    stream.index
                ));
                continue;
            }

            if cname == target_codec || allowed.contains(&cname) {
                already_target = true;
            }
        }

        if already_target {
            if media.container() != container {
                result.note(format!(
                    "File is {target_codec} but not in a {container} container. Remuxing."
                ));
                result.process_file = true;
                result.output_args = vec!["-map".to_string(), "0".to_string()];
                result
                    .output_args
                    .extend(["-c".to_string(), "copy".to_string()]);
                result.output_args.extend(remux_extra);
                result.requeue = true;
                return Ok(result);
            }
            if force_conform && !remux_extra.is_empty() {
                result.note(format!(
                    "File is already {target_codec}, but force_conform requires a remux."
                ));
                result.process_file = true;
                result.output_args = vec!["-map".to_string(), "0".to_string()];
                result
                    .output_args
                    .extend(["-c".to_string(), "copy".to_string()]);
                result.output_args.extend(remux_extra);
                result.requeue = true;
                return Ok(result);
            }
            result.note(format!(
                "File is already {target_codec} and in a {container} container."
            ));
            return Ok(result);
        }

        result.note(format!(
            "File is not in {target_codec}. Transcoding with {}.",
            encoder.encoder
        ));

        let mut input_args = encoder.input_args.clone();
        if VIDEOTOOLBOX_ENCODERS.contains(&encoder.encoder.as_str()) && self.config.hardware_decode
        {
            let mut args = vec!["-hwaccel".to_string(), "videotoolbox".to_string()];
            args.extend(input_args);
            input_args = args;
            result.note("Hardware (VideoToolbox) decoding enabled.");
        }
        if container == "ts" || container == "avi" {
            input_args.extend(["-fflags".to_string(), "+genpts".to_string()]);
        }

        let mut output_args = vec![
            "-map".to_string(),
            "0".to_string(),
            "-c".to_string(),
            "copy".to_string(),
            "-c:v".to_string(),
            encoder.encoder.clone(),
        ];
        output_args.extend(encoder.output_args.clone());
        if encoder.encoder.starts_with("hevc") && container == "mp4" {
            output_args.extend(["-tag:v".to_string(), "hvc1".to_string()]);
        }

        output_args.extend(self.video_encode_args(
            &encoder.encoder,
            &mode,
            target_bitrate.unwrap_or(0),
            minimum_bitrate,
            maximum_bitrate,
            self.quality_ceiling(media),
        ));

        if let Some(filter) = &encoder.video_filter {
            output_args.extend(["-vf".to_string(), filter.clone()]);
        }

        output_args.extend(["-max_muxing_queue_size".to_string(), "9999".to_string()]);
        output_args.extend(remux_extra);
        output_args.extend(encode_extra);

        result.input_args = input_args;
        result.output_args = output_args;
        result.process_file = true;
        result.requeue = true;
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encoder_codec_mapping() {
        assert_eq!(encoder_codec("hevc_videotoolbox"), Some("hevc"));
        assert_eq!(encoder_codec("h264_nvenc"), Some("h264"));
        assert_eq!(encoder_codec("libx265"), Some("hevc"));
        assert_eq!(encoder_codec("nope"), None);
    }

    #[test]
    fn conform_lists() {
        assert!(conform_codecs("mkv").contains(&"eia_608"));
        assert!(conform_codecs("mp4").contains(&"subrip"));
        assert!(conform_codecs("webm").is_empty());
    }
}
