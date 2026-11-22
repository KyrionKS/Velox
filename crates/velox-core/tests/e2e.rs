//! End-to-end tests that run the pipeline against a real ffmpeg/ffprobe.
//!
//! Every test creates its own media inside a [`TempDir`]; nothing outside the
//! temporary directory is touched.  When ffmpeg is not available the tests
//! print a skip note and return without failing.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;

use tempfile::TempDir;
use tokio_util::sync::CancellationToken;

use velox_core::config::Config;
use velox_core::events::NoopSink;
use velox_core::ffmpeg::{Ffmpeg, FfmpegBackend};
use velox_core::media::{AUDIO, SUBTITLE};
use velox_core::pipeline::{Pipeline, ProcessOptions, RunReport};

fn find_tool(name: &str) -> Option<PathBuf> {
    let env_key = format!("VELOX_TEST_{}", name.to_uppercase());
    if let Ok(value) = std::env::var(&env_key) {
        let candidate = PathBuf::from(value);
        if candidate.is_file() {
            return Some(candidate);
        }
    }

    if let Some(path) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&path) {
            let candidate = dir.join(name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }

    let fallback = PathBuf::from("/tmp/opencode/ffbin").join(name);
    if fallback.is_file() {
        return Some(fallback);
    }
    None
}

fn tools() -> Option<(PathBuf, PathBuf)> {
    Some((find_tool("ffmpeg")?, find_tool("ffprobe")?))
}

fn run_ffmpeg(ffmpeg: &Path, args: &[&str]) {
    let output = Command::new(ffmpeg)
        .args(["-y", "-hide_banner", "-loglevel", "error"])
        .args(args)
        .output()
        .expect("failed to start ffmpeg");
    assert!(
        output.status.success(),
        "ffmpeg failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

/// h264 video + eng/deu eac3 audio + eng subtitle, audio stream first.
fn make_messy_sample(ffmpeg: &Path, path: &Path) {
    let subtitle = path.with_extension("srt");
    std::fs::write(&subtitle, "1\n00:00:00,000 --> 00:00:02,000\nHello\n").expect("write subtitle");
    let subtitle_arg = subtitle.to_string_lossy().into_owned();
    let output = path.to_string_lossy().into_owned();

    run_ffmpeg(
        ffmpeg,
        &[
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=15:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=2",
            "-i",
            &subtitle_arg,
            "-map",
            "1:a",
            "-map",
            "0:v",
            "-map",
            "2:a",
            "-map",
            "3:s",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "eac3",
            "-b:a",
            "128k",
            "-c:s",
            "srt",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata:s:a:1",
            "language=deu",
            "-metadata:s:s:0",
            "language=eng",
            &output,
        ],
    );

    let _ = std::fs::remove_file(&subtitle);
}

/// h264 video + eng aac audio, video first.
fn make_simple_sample(ffmpeg: &Path, path: &Path) {
    let output = path.to_string_lossy().into_owned();
    run_ffmpeg(
        ffmpeg,
        &[
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=15:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-metadata:s:a:0",
            "language=eng",
            &output,
        ],
    );
}

/// hevc video + eng aac audio, already in mkv.
fn make_hevc_sample(ffmpeg: &Path, path: &Path) {
    let output = path.to_string_lossy().into_owned();
    run_ffmpeg(
        ffmpeg,
        &[
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=15:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx265",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-metadata:s:a:0",
            "language=eng",
            &output,
        ],
    );
}

/// h264 video + a 5.1 E-AC-3 audio track.
fn make_surround_sample(ffmpeg: &Path, path: &Path) {
    let output = path.to_string_lossy().into_owned();
    run_ffmpeg(
        ffmpeg,
        &[
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=15:duration=2",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=sin(440*2*PI*t)|sin(550*2*PI*t)|sin(660*2*PI*t)|sin(60*2*PI*t)|sin(770*2*PI*t)|sin(880*2*PI*t):s=48000:d=2:c=5.1",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "eac3",
            "-b:a",
            "640k",
            &output,
        ],
    );
}

fn make_m4a_sample(ffmpeg: &Path, path: &Path) {
    let output = path.to_string_lossy().into_owned();
    run_ffmpeg(
        ffmpeg,
        &[
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            &output,
        ],
    );
}

fn base_config(ffmpeg: &Path, ffprobe: &Path) -> Config {
    let mut config = Config::default();
    config.general.ffmpeg_path = ffmpeg.to_string_lossy().into_owned();
    config.general.ffprobe_path = ffprobe.to_string_lossy().into_owned();
    config.general.temp_dir = "source".to_string();
    config.general.keep_original = false;
    config.general.verify_output = true;
    config.general.verify_full_decode = false;
    config.general.write_report = true;
    config.plugins.file_size_check.upper_bound = 999.0;
    config.plugins.file_size_check.lower_bound = 0.0;
    config.plugins.hevc_gpu.allow_cpu_fallback = true;
    config.plugins.hevc_gpu.encoder = "auto".to_string();
    config.plugins.hevc_gpu.cpu_preset = "ultrafast".to_string();
    config
}

async fn run_pipeline(config: Config, path: &Path, dry_run: bool) -> (RunReport, Arc<Ffmpeg>) {
    let backend = Arc::new(
        Ffmpeg::resolve(&config.general.ffmpeg_path, &config.general.ffprobe_path)
            .expect("resolve ffmpeg/ffprobe"),
    );
    let pipeline = Pipeline::new(config, backend.clone());
    let options = ProcessOptions {
        dry_run,
        cancel: CancellationToken::new(),
    };
    let report = pipeline.process(path, &options, Arc::new(NoopSink)).await;
    (report, backend)
}

fn skip() {
    eprintln!("skip: ffmpeg not available");
}

#[tokio::test]
async fn e2e_messy_mkv_cleans_reorders_and_reports() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("messy.mkv");
    make_messy_sample(&ffmpeg, &source);

    let config = base_config(&ffmpeg, &ffprobe);
    let (report, backend) = run_pipeline(config, &source, false).await;

    assert!(report.ok(), "pipeline error: {:?}", report.error);
    assert!(report.modified, "expected the file to be modified");
    let final_path = report.final_path.as_ref().expect("final path");
    assert!(final_path.exists(), "final file must exist");

    let media = backend.probe(final_path).await.expect("probe final file");
    assert_eq!(
        media
            .streams
            .first()
            .map(|stream| stream.codec_type.as_str()),
        Some("video"),
        "video must be the first stream"
    );

    let audio = media.streams_of(AUDIO);
    assert_eq!(audio.len(), 1, "deu audio should have been removed");
    assert_eq!(audio[0].language(), "eng");
    assert_eq!(audio[0].codec(), "aac");

    let subtitles = media.streams_of(SUBTITLE);
    assert_eq!(subtitles.len(), 1, "eng subtitle should be kept");
    assert_eq!(subtitles[0].language(), "eng");

    let report_path = report.report_path.as_ref().expect("report path");
    assert!(report_path.exists(), "report file must exist");
}

#[tokio::test]
async fn e2e_surround_eac3_to_aac_keeps_5_1_layout() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };

    let mut config = base_config(&ffmpeg, &ffprobe);
    config.plugins.clean_audio.enabled = false;
    config.plugins.clean_subs.enabled = false;
    config.plugins.reorder_all_streams.enabled = false;
    config.plugins.hevc_gpu.enabled = false;
    config.plugins.file_size_check.enabled = false;
    config.plugins.standardise_audio.audio_codec = "aac".to_string();
    config.plugins.standardise_audio.aac_encoder = "aac".to_string();
    config.plugins.standardise_audio.audio_bitrate_kbps = 0;

    let check = Arc::new(
        Ffmpeg::resolve(&config.general.ffmpeg_path, &config.general.ffprobe_path)
            .expect("resolve ffmpeg/ffprobe"),
    );
    if !check.has_audio_encoder("eac3").await {
        eprintln!("skip: eac3 encoder not available");
        return;
    }

    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("surround.mkv");
    make_surround_sample(&ffmpeg, &source);

    let (report, backend) = run_pipeline(config, &source, false).await;
    assert!(report.ok(), "pipeline error: {:?}", report.error);
    let final_path = report.final_path.as_ref().expect("final path");
    let media = backend.probe(final_path).await.expect("probe final file");

    let audio = media.streams_of(AUDIO);
    assert_eq!(audio.len(), 1);
    assert_eq!(audio[0].codec(), "aac");
    assert_eq!(audio[0].channels, Some(6));
    let layout = audio[0].channel_layout.as_deref().unwrap_or("");
    assert!(!layout.is_empty(), "channel layout must be present");
    assert!(
        !layout.to_lowercase().contains("pce"),
        "channel layout must not be a PCE: {layout}"
    );
    assert!(
        layout.starts_with("5.1"),
        "expected a 5.1 layout, got {layout}"
    );
}

#[tokio::test]
async fn e2e_already_hevc_mkv_is_unchanged() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("already.mkv");
    make_hevc_sample(&ffmpeg, &source);
    let original = std::fs::read(&source).expect("read source");

    let config = base_config(&ffmpeg, &ffprobe);
    let (report, _backend) = run_pipeline(config, &source, false).await;

    assert!(report.ok(), "pipeline error: {:?}", report.error);
    assert!(!report.modified, "already HEVC/MKV must not be modified");
    assert_eq!(
        std::fs::read(&source).expect("read source"),
        original,
        "original file must stay byte-identical"
    );
}

#[tokio::test]
async fn e2e_non_video_m4a_is_skipped() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("audio.m4a");
    make_m4a_sample(&ffmpeg, &source);
    let original = std::fs::read(&source).expect("read source");

    let (report, _backend) = run_pipeline(base_config(&ffmpeg, &ffprobe), &source, false).await;

    assert!(report.ok(), "pipeline error: {:?}", report.error);
    assert!(report.skipped);
    assert_eq!(report.reason, "not a video file");
    assert!(!report.modified);
    assert_eq!(std::fs::read(&source).expect("read source"), original);
}

#[tokio::test]
async fn e2e_container_change_mkv_to_mp4() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("sample.mkv");
    make_simple_sample(&ffmpeg, &source);

    let mut config = base_config(&ffmpeg, &ffprobe);
    config.plugins.hevc_gpu.container = "mp4".to_string();

    let (report, _backend) = run_pipeline(config, &source, false).await;
    assert!(report.ok(), "pipeline error: {:?}", report.error);
    assert!(report.modified);

    let final_path = report.final_path.as_ref().expect("final path");
    assert_eq!(final_path.extension().and_then(|e| e.to_str()), Some("mp4"));
    assert!(final_path.exists(), "mp4 target must exist");
    assert!(!source.exists(), "original mkv must be removed");

    let report_path = report.report_path.as_ref().expect("report path");
    assert!(report_path.exists());
    assert_eq!(report_path.parent(), final_path.parent());
}

#[tokio::test]
async fn e2e_output_dir_keeps_source_and_avoids_overwrite() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("sample.mkv");
    make_simple_sample(&ffmpeg, &source);
    let original = std::fs::read(&source).expect("read source");

    let out_dir = tmp.path().join("out");
    std::fs::create_dir_all(&out_dir).expect("create output dir");
    let existing = out_dir.join("sample.mkv");
    std::fs::write(&existing, b"USER FILE").expect("write existing target");

    let mut config = base_config(&ffmpeg, &ffprobe);
    config.general.output_dir = out_dir.to_string_lossy().into_owned();

    let (report, _backend) = run_pipeline(config, &source, false).await;
    assert!(report.ok(), "pipeline error: {:?}", report.error);
    assert!(report.modified);

    let final_path = report.final_path.as_ref().expect("final path");
    assert_eq!(final_path.parent(), Some(out_dir.as_path()));
    assert_eq!(
        final_path.file_name().and_then(|name| name.to_str()),
        Some("sample (1).mkv")
    );
    assert_eq!(
        std::fs::read(&existing).expect("read existing"),
        b"USER FILE",
        "existing file must not be overwritten"
    );
    assert!(source.exists(), "source must be left in place");
    assert_eq!(std::fs::read(&source).expect("read source"), original);
}

#[tokio::test]
async fn e2e_dry_run_writes_nothing() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("messy.mkv");
    make_messy_sample(&ffmpeg, &source);
    let original = std::fs::read(&source).expect("read source");
    let entries_before = std::fs::read_dir(tmp.path()).expect("read dir").count();

    let (report, _backend) = run_pipeline(base_config(&ffmpeg, &ffprobe), &source, true).await;

    assert!(report.ok(), "pipeline error: {:?}", report.error);
    assert!(!report.modified, "dry run must not report a modification");
    assert!(report.would_change, "dry run must report planned changes");
    assert!(
        !report.passes.is_empty() && report.passes.iter().all(|pass| pass.planned),
        "dry-run passes must be marked as planned"
    );
    assert_eq!(std::fs::read(&source).expect("read source"), original);
    let entries_after = std::fs::read_dir(tmp.path()).expect("read dir").count();
    assert_eq!(
        entries_after, entries_before,
        "dry run must not write files"
    );
}

#[tokio::test]
async fn e2e_verification_populates_final_media() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("verify.mkv");
    make_simple_sample(&ffmpeg, &source);

    let (report, backend) = run_pipeline(base_config(&ffmpeg, &ffprobe), &source, false).await;
    assert!(report.ok(), "pipeline error: {:?}", report.error);
    assert!(report.modified);

    let final_path = report.final_path.as_ref().expect("final path");
    assert!(final_path.exists());
    let final_media = report.final_media.as_ref().expect("final media");
    assert!(!final_media.streams.is_empty());
    let probed = backend
        .probe(final_path)
        .await
        .expect("probe final file after run");
    assert_eq!(probed.streams.len(), final_media.streams.len());
}

#[tokio::test]
async fn e2e_file_size_error_keeps_original() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("sample.mkv");
    make_simple_sample(&ffmpeg, &source);
    let original = std::fs::read(&source).expect("read source");

    let mut config = base_config(&ffmpeg, &ffprobe);
    config.plugins.file_size_check.upper_bound = 1.0;
    let (report, _backend) = run_pipeline(config, &source, false).await;

    assert!(report.error.is_some(), "size check must fail");
    assert!(!report.ok());
    assert!(source.exists(), "original must stay in place");
    assert_eq!(
        std::fs::read(&source).expect("read source"),
        original,
        "original bytes must be unchanged"
    );
    let backup = source.with_file_name("sample.velox-orig.mkv");
    assert!(!backup.exists(), "no backup must be created on error");

    let report_path = report.report_path.as_ref().expect("report path");
    assert!(report_path.exists(), "report file must exist");
    let text = std::fs::read_to_string(report_path).expect("read report");
    assert!(text.contains("ERROR"), "report must contain ERROR");
}

#[tokio::test]
async fn e2e_scratch_and_backup_are_cleaned() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("sample.mkv");
    make_simple_sample(&ffmpeg, &source);
    let original = std::fs::read(&source).expect("read source");

    let has_scratch = || {
        std::fs::read_dir(tmp.path())
            .expect("read dir")
            .filter_map(|entry| entry.ok())
            .any(|entry| {
                entry.file_type().map(|kind| kind.is_dir()).unwrap_or(false)
                    && entry.file_name().to_string_lossy().starts_with(".velox-")
            })
    };
    assert!(!has_scratch(), "no scratch dirs before the run");

    let mut config = base_config(&ffmpeg, &ffprobe);
    config.general.keep_original = false;
    let (report, _backend) = run_pipeline(config, &source, false).await;

    assert!(report.ok(), "pipeline error: {:?}", report.error);
    assert!(report.modified, "expected the file to be modified");
    assert!(!has_scratch(), "scratch dirs must be cleaned up");

    let entries: Vec<String> = std::fs::read_dir(tmp.path())
        .expect("read dir")
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    assert!(
        !entries.iter().any(|name| name.contains(".velox-orig")),
        "no backup file must remain: {entries:?}"
    );
    assert_ne!(
        std::fs::read(&source).expect("read source"),
        original,
        "original must have been replaced"
    );
}

#[tokio::test]
async fn e2e_keep_original_creates_backup() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("sample.mkv");
    make_simple_sample(&ffmpeg, &source);
    let original = std::fs::read(&source).expect("read source");

    let mut config = base_config(&ffmpeg, &ffprobe);
    config.general.keep_original = true;
    let (report, _backend) = run_pipeline(config, &source, false).await;

    assert!(report.ok(), "pipeline error: {:?}", report.error);
    let backup = source.with_file_name("sample.velox-orig.mkv");
    assert!(backup.exists(), "backup file must exist");
    assert_eq!(
        std::fs::read(&backup).expect("read backup"),
        original,
        "backup must hold the original bytes"
    );
    let final_path = report.final_path.as_ref().expect("final path");
    assert!(final_path.exists(), "final file must exist");
}

#[tokio::test]
async fn e2e_remux_plugins_merge_into_one_pass() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("messy.mkv");
    make_messy_sample(&ffmpeg, &source);

    let (report, _backend) = run_pipeline(base_config(&ffmpeg, &ffprobe), &source, false).await;
    assert!(report.ok(), "pipeline error: {:?}", report.error);
    assert_eq!(report.passes.len(), 2, "expected exactly two passes");
    assert_eq!(report.passes[0].kind, "remux");
    assert!(
        report.passes[0].steps.len() >= 2,
        "remux pass must merge several plugins"
    );
    assert_eq!(report.passes[1].kind, "transcode");
}

#[tokio::test]
async fn e2e_cancel_before_start_keeps_original() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("sample.mkv");
    make_simple_sample(&ffmpeg, &source);
    let original = std::fs::read(&source).expect("read source");

    let config = base_config(&ffmpeg, &ffprobe);
    let backend = Arc::new(
        Ffmpeg::resolve(&config.general.ffmpeg_path, &config.general.ffprobe_path)
            .expect("resolve ffmpeg/ffprobe"),
    );
    let pipeline = Pipeline::new(config, backend);
    let cancel = CancellationToken::new();
    cancel.cancel();
    let options = ProcessOptions {
        dry_run: false,
        cancel,
    };
    let report = pipeline
        .process(&source, &options, Arc::new(NoopSink))
        .await;

    assert!(report.cancelled, "report must be marked cancelled");
    assert!(!report.ok());
    assert_eq!(
        std::fs::read(&source).expect("read source"),
        original,
        "original must be unchanged"
    );
    assert!(
        !source.with_file_name("sample.velox-orig.mkv").exists(),
        "cancelled run must not create a backup"
    );
    // The current pipeline writes a report for cancellations so the user can
    // see why the file was not replaced.
    let report_path = report.report_path.as_ref().expect("cancellation report");
    assert!(report_path.exists(), "cancellation report must exist");
    let text = std::fs::read_to_string(report_path).expect("read report");
    assert!(
        text.contains("cancelled"),
        "report must mark the cancellation"
    );
}

#[cfg(all(target_os = "macos", target_arch = "aarch64"))]
#[tokio::test]
async fn e2e_macos_videotoolbox_quality_mode() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };

    let check = Arc::new(
        Ffmpeg::resolve(
            ffmpeg.to_string_lossy().as_ref(),
            ffprobe.to_string_lossy().as_ref(),
        )
        .expect("resolve ffmpeg/ffprobe"),
    );
    if !check.has_encoder("hevc_videotoolbox", &[], None).await {
        eprintln!("skip: hevc_videotoolbox not available");
        return;
    }

    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("sample.mkv");
    make_simple_sample(&ffmpeg, &source);

    let mut config = base_config(&ffmpeg, &ffprobe);
    config.plugins.hevc_gpu.encoder = "hevc_videotoolbox".to_string();
    config.plugins.hevc_gpu.bitrate_mode = "quality".to_string();
    config.plugins.hevc_gpu.quality = 50;

    let (report, backend) = run_pipeline(config, &source, false).await;
    assert!(report.ok(), "pipeline error: {:?}", report.error);
    let final_path = report.final_path.as_ref().expect("final path");
    let media = backend.probe(final_path).await.expect("probe final file");
    let video = media.video_stream().expect("video stream");
    assert_eq!(video.codec(), "hevc");

    let transcode = report
        .passes
        .iter()
        .find(|pass| pass.kind == "transcode")
        .expect("transcode pass");
    assert!(
        transcode.command.windows(2).any(|pair| pair[0] == "-q:v"),
        "quality mode must pass -q:v"
    );
}

#[tokio::test]
async fn e2e_successful_report_skips_file_on_the_next_run() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("sample.mkv");
    make_simple_sample(&ffmpeg, &source);

    let (first, _backend) = run_pipeline(base_config(&ffmpeg, &ffprobe), &source, false).await;
    assert!(first.ok(), "pipeline error: {:?}", first.error);
    assert!(first.modified);
    let report = first.report_path.as_ref().expect("first report");
    assert!(report.exists());
    let processed_bytes = std::fs::read(&source).expect("read processed file");

    let (second, _backend) = run_pipeline(base_config(&ffmpeg, &ffprobe), &source, false).await;
    assert!(second.ok(), "pipeline error: {:?}", second.error);
    assert!(second.skipped, "second run must be skipped");
    assert_eq!(second.reason, "already processed");
    assert!(!second.modified);
    assert_eq!(second.report_path.as_deref(), Some(report.as_path()));
    assert_eq!(
        std::fs::read(&source).expect("read source"),
        processed_bytes,
        "skipped file must stay untouched"
    );
}

#[tokio::test]
async fn e2e_container_change_report_marks_the_output_as_processed() {
    let Some((ffmpeg, ffprobe)) = tools() else {
        skip();
        return;
    };
    let tmp = TempDir::new().expect("tempdir");
    let source = tmp.path().join("sample.mkv");
    make_simple_sample(&ffmpeg, &source);

    let mut config = base_config(&ffmpeg, &ffprobe);
    config.plugins.hevc_gpu.container = "mp4".to_string();
    let (first, _backend) = run_pipeline(config.clone(), &source, false).await;
    assert!(first.ok(), "pipeline error: {:?}", first.error);
    let output = first.final_path.as_ref().expect("mp4 output").clone();
    assert_eq!(
        output.extension().and_then(|value| value.to_str()),
        Some("mp4")
    );
    assert!(!source.exists());

    let (second, _backend) = run_pipeline(config, &output, false).await;
    assert!(second.ok(), "pipeline error: {:?}", second.error);
    assert!(second.skipped, "output must be recognised as processed");
    assert_eq!(second.reason, "already processed");
}
