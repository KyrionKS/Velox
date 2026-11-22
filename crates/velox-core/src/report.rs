//! Human readable processing report (`<name>-report.txt`).

use std::path::{Path, PathBuf};

use crate::config::Config;
use crate::events::fmt_bytes;
use crate::media::{stream_language, stream_type, MediaInfo, AUDIO, SUBTITLE, VIDEO};
use crate::pipeline::RunReport;
use crate::plugins::RemuxPlan;

const WIDTH: usize = 62;
const MINUS: char = '\u{2212}';

fn rule(character: char) -> String {
    character.to_string().repeat(WIDTH)
}

fn heading(title: &str) -> Vec<String> {
    vec![
        String::new(),
        rule('\u{2500}'),
        format!("  {title}"),
        rule('\u{2500}'),
    ]
}

fn duration(seconds: f64) -> String {
    let total = seconds.max(0.0) as u64;
    let hours = total / 3600;
    let minutes = (total % 3600) / 60;
    let secs = total % 60;
    format!("{hours}:{minutes:02}:{secs:02}")
}

fn fps(stream: &crate::media::Stream) -> String {
    let raw = stream
        .avg_frame_rate
        .as_deref()
        .or(stream.r_frame_rate.as_deref())
        .unwrap_or("");
    if raw.is_empty() || raw == "0/0" || raw == "0" {
        return String::new();
    }
    let mut parts = raw.split('/');
    let numerator = parts.next().and_then(|value| value.parse::<f64>().ok());
    let denominator = parts
        .next()
        .and_then(|value| value.parse::<f64>().ok())
        .unwrap_or(1.0);
    let Some(numerator) = numerator else {
        return String::new();
    };
    if denominator == 0.0 {
        return String::new();
    }
    let value = numerator / denominator;
    if value <= 0.0 {
        return String::new();
    }
    let text = format!("{value:.3}");
    let text = text.trim_end_matches('0').trim_end_matches('.');
    format!("{text}fps")
}

fn is_10bit(stream: &crate::media::Stream) -> bool {
    let profile = stream.profile.clone().unwrap_or_default().to_lowercase();
    profile == "high 10"
        || profile == "main 10"
        || stream.bits_per_raw_sample.as_deref() == Some("10")
        || stream.pix_fmt.as_deref() == Some("yuv420p10le")
}

fn channels(count: u32) -> String {
    match count {
        8 => "7.1".to_string(),
        6 => "5.1".to_string(),
        2 => "2.0".to_string(),
        1 => "1.0".to_string(),
        other => other.to_string(),
    }
}

fn stream_line(stream: &crate::media::Stream) -> String {
    let kind = stream_type(stream);
    let codec = stream.codec_name.clone().unwrap_or_else(|| "?".to_string());
    let mut parts: Vec<String> = Vec::new();
    match kind {
        VIDEO => {
            if let (Some(width), Some(height)) = (stream.width, stream.height) {
                parts.push(format!("{width}x{height}"));
            }
            let rate = fps(stream);
            if !rate.is_empty() {
                parts.push(rate);
            }
            if is_10bit(stream) {
                parts.push("10-bit".to_string());
            }
        }
        AUDIO => {
            if let Some(count) = stream.channels {
                parts.push(channels(count));
            }
            let language = stream_language(stream);
            parts.push(if language.is_empty() {
                "und".to_string()
            } else {
                language
            });
            if stream.is_default() {
                parts.push("(default)".to_string());
            }
        }
        SUBTITLE => {
            let language = stream_language(stream);
            parts.push(if language.is_empty() {
                "und".to_string()
            } else {
                language
            });
            if stream.is_default() {
                parts.push("(default)".to_string());
            }
        }
        _ => {}
    }
    format!(
        "    #{:<3} {:<9} {:<10} {}",
        stream.index,
        kind,
        codec,
        parts.join("  ")
    )
    .trim_end()
    .to_string()
}

fn stream_table(media: &MediaInfo) -> Vec<String> {
    media.streams.iter().map(stream_line).collect()
}

fn media_block(media: &MediaInfo) -> Vec<String> {
    let mut counts = std::collections::BTreeMap::new();
    for stream in &media.streams {
        *counts.entry(stream_type(stream)).or_insert(0usize) += 1;
    }
    let summary = [VIDEO, AUDIO, SUBTITLE]
        .iter()
        .filter_map(|kind| {
            counts
                .get(kind)
                .map(|count| format!("{} {}", capitalize(kind), count))
        })
        .collect::<Vec<_>>()
        .join(" \u{b7} ");

    let container = media.container();
    let mut lines = vec![format!(
        "  Container      {}        Size {}        Duration {}",
        if container.is_empty() {
            "?"
        } else {
            &container
        },
        fmt_bytes(media.size),
        duration(media.duration())
    )];
    if !summary.is_empty() {
        lines.push(format!("  Streams        {summary}"));
    }
    lines.extend(stream_table(media));
    lines
}

fn capitalize(value: &str) -> String {
    let mut chars = value.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

fn plan_bullets(plan: &RemuxPlan, media: &MediaInfo) -> Vec<String> {
    let mut bullets = Vec::new();
    for index in &plan.drop {
        let stream = media.streams.iter().find(|stream| stream.index == *index);
        let kind = stream
            .map(|stream| match stream_type(stream) {
                VIDEO => "Video",
                AUDIO => "Audio",
                SUBTITLE => "Subtitle",
                "data" => "Data",
                _ => "Stream",
            })
            .unwrap_or("Stream");
        let language = stream.map(stream_language).unwrap_or_default();
        let mut label = format!("{kind} track #{index}");
        if !language.is_empty() && language != "und" {
            label.push_str(&format!(" ({language})"));
        }
        bullets.push(format!("{label} removed"));
    }
    for (index, codec) in &plan.codecs {
        let old = media
            .streams
            .iter()
            .find(|stream| stream.index == *index)
            .map(|stream| stream.codec())
            .unwrap_or_else(|| "?".to_string());
        let bitrate = plan
            .bitrates
            .get(index)
            .map(|value| format!(", {value}"))
            .unwrap_or_default();
        bullets.push(format!("Track #{index}: {old} \u{2192} {codec}{bitrate}"));
    }
    for (index, value) in &plan.dispositions {
        if value == "default" {
            bullets.push(format!("Track #{index} marked as default"));
        }
    }
    for (index, items) in &plan.metadata {
        for (key, value) in items {
            bullets.push(format!("Track #{index}: {key}={value}"));
        }
    }
    if plan.order.is_some() {
        bullets.push("Stream order adjusted".to_string());
    }
    bullets
}

fn encoder_from_command(command: &[String]) -> Option<String> {
    command
        .windows(2)
        .find(|pair| pair[0] == "-c:v")
        .map(|pair| pair[1].clone())
}

fn mode_label(config: &Config, encoder: Option<&str>) -> String {
    let hevc = &config.plugins.hevc_gpu;
    let encoder = encoder.unwrap_or(&hevc.encoder);
    match hevc.bitrate_mode.as_str() {
        "quality" => format!("constant quality {}, {encoder}", hevc.quality),
        "size" => format!("target size {:.0} MiB, {encoder}", hevc.target_size_mb),
        _ => format!("automatic ({:.3} bpp), {encoder}", hevc.bits_per_pixel),
    }
}

fn step_lines(report: &RunReport, config: &Config) -> Vec<String> {
    let mut lines = Vec::new();
    let total = report.passes.len();
    for (number, event) in report.passes.iter().enumerate() {
        let saved = event.saved_bytes();
        let percent = if event.input_bytes > 0 {
            saved as f64 / event.input_bytes as f64 * 100.0
        } else {
            0.0
        };
        let sign = if saved < 0 {
            "+".to_string()
        } else {
            MINUS.to_string()
        };
        lines.push(format!("  {}/{}  {}", number + 1, total, event.label()));
        lines.push(format!(
            "        {} \u{2192} {}   ({}{}, {:.1} %)",
            fmt_bytes(event.input_bytes),
            fmt_bytes(event.output_bytes),
            sign,
            fmt_bytes(saved.unsigned_abs()),
            percent
        ));
        if event.kind == "transcode" {
            let encoder = encoder_from_command(&event.command);
            lines.push(format!(
                "        \u{2022} Video re-encoded ({})",
                mode_label(config, encoder.as_deref())
            ));
        } else if let (Some(plan), Some(original)) = (&event.plan, &report.original_media) {
            for bullet in plan_bullets(plan, original) {
                lines.push(format!("        \u{2022} {bullet}"));
            }
        }
        lines.push(String::new());
    }
    lines
}

fn settings_block(config: &Config) -> Vec<String> {
    let hevc = &config.plugins.hevc_gpu;
    let verify = if config.general.verify_output {
        if config.general.verify_full_decode {
            "yes (full decode)"
        } else {
            "yes (basic)"
        }
    } else {
        "no"
    };
    vec![
        format!("  Mode           {}", mode_label(config, None)),
        format!("  Container      {}", hevc.container),
        format!("  Verification   {verify}"),
        format!(
            "  Audio          {} \u{b7} languages {}",
            config.plugins.standardise_audio.audio_codec, config.plugins.clean_audio.language
        ),
        format!(
            "  Subtitles      languages {}",
            config.plugins.clean_subs.language
        ),
    ]
}

fn result_text(report: &RunReport) -> &'static str {
    if report.cancelled {
        return "cancelled";
    }
    if report.error.is_some() {
        return "error";
    }
    if report.modified {
        if report.output_dir.is_some() {
            return "new file written (original untouched)";
        }
        return if report.kept_original {
            "replaced (original kept)"
        } else {
            "replaced (original removed)"
        };
    }
    "unchanged"
}

pub fn build_report(report: &RunReport, config: &Config) -> String {
    let now = chrono::Local::now().format("%Y-%m-%d %H:%M").to_string();
    let mut lines = vec![
        rule('\u{2550}'),
        "  VELOX \u{2013} Processing report".to_string(),
        rule('\u{2550}'),
        String::new(),
        format!(
            "  File           {}",
            report
                .source
                .file_name()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default()
        ),
        format!("  Processed      {now}"),
    ];
    if report.elapsed > 0.0 {
        lines.push(format!("  Duration       {}", duration(report.elapsed)));
    }
    lines.push(format!("  Result         {}", result_text(report)));

    if let Some(original) = &report.original_media {
        lines.extend(heading("BEFORE"));
        lines.extend(media_block(original));
    }

    if !report.passes.is_empty() {
        lines.extend(heading("STEPS"));
        lines.extend(step_lines(report, config));
    }

    if report.modified {
        if let Some(final_media) = &report.final_media {
            lines.extend(heading("AFTER"));
            lines.push(format!("  Size           {}", fmt_bytes(final_media.size)));
            lines.extend(stream_table(final_media));
        }
    }

    if report.original_bytes > 0 && report.final_bytes > 0 && report.modified {
        let saved = report.saved_bytes();
        let percent = saved as f64 / report.original_bytes as f64 * 100.0;
        let sign = if saved < 0 {
            "+".to_string()
        } else {
            MINUS.to_string()
        };
        lines.extend(heading("TOTAL"));
        lines.push(format!(
            "  {} \u{2192} {}   ({}{}, {:.1} %)",
            fmt_bytes(report.original_bytes),
            fmt_bytes(report.final_bytes),
            sign,
            fmt_bytes(saved.unsigned_abs()),
            percent
        ));
        lines.push(String::new());
        lines.extend(settings_block(config));
    }

    if let Some(error) = &report.error {
        lines.extend(heading("ERROR"));
        lines.push(format!("  {error}"));
    }

    lines.push(String::new());
    lines.push(rule('\u{2550}'));
    lines.push(String::new());
    lines.join("\n")
}

/// The report location for a source file (next to it, same stem).
pub fn source_report_path(path: &Path) -> PathBuf {
    path.with_file_name(format!(
        "{}-report.txt",
        path.file_stem()
            .map(|value| value.to_string_lossy().into_owned())
            .unwrap_or_default()
    ))
}

/// Return the report of a successful earlier run, when it is still current.
///
/// A report is ignored when it is older than the media file (the file was
/// changed afterwards) or when it documents a failure/cancellation, so failed
/// runs never make a file look "already processed".
pub fn existing_report(path: &Path) -> Option<PathBuf> {
    let report = source_report_path(path);
    let report_meta = std::fs::metadata(&report).ok()?;
    if !report_meta.is_file() {
        return None;
    }
    if let (Ok(report_time), Ok(file_time)) = (
        report_meta.modified(),
        std::fs::metadata(path).and_then(|meta| meta.modified()),
    ) {
        if report_time < file_time {
            return None;
        }
    }
    let text = std::fs::read_to_string(&report).ok()?;
    if !text.contains("VELOX") {
        return None;
    }
    if text.contains("\n  ERROR\n") || text.contains("\n  FEHLER\n") {
        return None;
    }

    // The report names its output file. This avoids treating a sibling with the
    // same stem (for example movie.mkv next to a processed movie.mp4) as done.
    let file_name = path.file_name()?.to_string_lossy();
    let reported_name = text.lines().find_map(|line| {
        let line = line.trim_start();
        for label in ["File", "Datei"] {
            if let Some(rest) = line.strip_prefix(label) {
                let rest = rest.trim();
                if !rest.is_empty() {
                    return Some(rest);
                }
            }
        }
        None
    });
    let matches = match reported_name {
        Some(reported) if reported == file_name => true,
        // A container change moves the report next to a sibling (e.g. movie.mp4)
        // while the report still names the original (movie.mkv). Accept that
        // only when the original named file is gone, so two real siblings do
        // not mark each other as processed.
        Some(reported) => {
            Path::new(reported).file_stem() == path.file_stem()
                && !path.with_file_name(reported).exists()
        }
        None => false,
    };
    if !matches {
        return None;
    }
    Some(report)
}

pub fn report_path_for(report: &RunReport) -> PathBuf {
    let base = report
        .final_path
        .as_deref()
        .unwrap_or(report.source.as_path());
    base.with_file_name(format!(
        "{}-report.txt",
        base.file_stem()
            .map(|value| value.to_string_lossy().into_owned())
            .unwrap_or_default()
    ))
}

pub fn write_report(report: &RunReport, config: &Config) -> std::io::Result<PathBuf> {
    let path = report_path_for(report);
    std::fs::write(&path, build_report(report, config))?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::PassEvent;
    use crate::media::{FormatInfo, ProbeData, Stream};

    fn sample_report() -> RunReport {
        let media = MediaInfo::from_probe(
            "/movies/Test.mkv",
            ProbeData {
                streams: vec![
                    Stream {
                        index: 0,
                        codec_type: "video".into(),
                        codec_name: Some("hevc".into()),
                        width: Some(1920),
                        height: Some(1080),
                        avg_frame_rate: Some("24000/1001".into()),
                        ..Default::default()
                    },
                    Stream {
                        index: 1,
                        codec_type: "audio".into(),
                        codec_name: Some("aac".into()),
                        channels: Some(6),
                        tags: [("language".to_string(), serde_json::json!("eng"))]
                            .into_iter()
                            .collect(),
                        ..Default::default()
                    },
                ],
                format: FormatInfo {
                    duration: Some("120".into()),
                    ..Default::default()
                },
            },
            2_000_000,
        );
        RunReport {
            source: "/movies/Test.mkv".into(),
            final_path: Some("/movies/Test.mkv".into()),
            modified: true,
            skipped: false,
            cancelled: false,
            reason: String::new(),
            error: None,
            original_bytes: 2_000_000,
            final_bytes: 1_000_000,
            elapsed: 65.0,
            original_media: Some(media.clone()),
            final_media: Some(media),
            report_path: None,
            output_dir: None,
            kept_original: false,
            would_change: false,
            passes: vec![PassEvent {
                steps: vec!["Encode Video".into()],
                kind: "transcode".into(),
                input_bytes: 2_000_000,
                output_bytes: 1_000_000,
                command: vec!["ffmpeg".into(), "-c:v".into(), "hevc_videotoolbox".into()],
                plan: None,
                planned: false,
            }],
            outcomes: Vec::new(),
        }
    }

    #[test]
    fn existing_report_ignores_failures_and_stale_files() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("movie.mkv");
        std::fs::write(&source, b"video").unwrap();
        assert!(existing_report(&source).is_none());

        let report = source.with_file_name("movie-report.txt");
        std::fs::write(
            &report,
            "VELOX – Processing report\n  File           movie.mkv\n  Result         replaced\n",
        )
        .unwrap();
        assert_eq!(existing_report(&source), Some(report.clone()));

        std::fs::write(
            &report,
            "VELOX – Processing report\n  File           other.mkv\n  Result         replaced\n",
        )
        .unwrap();
        assert!(existing_report(&source).is_none());

        std::fs::write(
            &report,
            "VELOX – Processing report\n  File           movie.mkv\n  ERROR\n  boom\n",
        )
        .unwrap();
        assert!(existing_report(&source).is_none());
    }

    #[test]
    fn report_contains_sections() {
        let text = build_report(&sample_report(), &Config::default());
        assert!(text.contains("VELOX"));
        assert!(text.contains("BEFORE"));
        assert!(text.contains("STEPS"));
        assert!(text.contains("AFTER"));
        assert!(text.contains("TOTAL"));
        assert!(text.contains("hevc_videotoolbox"));
    }
}
