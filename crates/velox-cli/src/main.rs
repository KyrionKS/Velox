//! Velox command line interface.

use std::collections::HashSet;
use std::io::IsTerminal;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use clap::{ArgAction, Parser};
use tokio::sync::Semaphore;
use tokio::task::JoinSet;
use tokio_util::sync::CancellationToken;
use tracing_subscriber::filter::LevelFilter;

use velox_core::config::{Config, DEFAULT_ORDER};
use velox_core::events::{fmt_bytes, EventSink, PipelineEvent};
use velox_core::ffmpeg::{Ffmpeg, FfmpegBackend};
use velox_core::pipeline::{Pipeline, ProcessOptions, RunReport};
use velox_core::plugins::plugin_display_name;
use velox_core::VERSION;

const DEFAULT_EXTENSIONS: [&str; 15] = [
    "mkv", "mp4", "m4v", "avi", "mov", "ts", "m2ts", "mts", "webm", "wmv", "flv", "mpg", "mpeg",
    "vob", "mp2",
];

#[derive(Debug, Parser)]
#[command(
    name = "velox",
    version = VERSION,
    about = "Velox - clean up and transcode video files with ffmpeg"
)]
struct Args {
    /// Files and/or directories to process.
    paths: Vec<String>,

    /// Path to a Velox TOML config file.
    #[arg(short = 'c', long)]
    config: Option<PathBuf>,

    /// Recurse into directories.
    #[arg(short = 'r', long)]
    recursive: bool,

    /// Show what would be done without running ffmpeg.
    #[arg(short = 'n', long = "dry-run")]
    dry_run: bool,

    /// Only print stream information and exit.
    #[arg(long)]
    info: bool,

    /// Comma separated extensions to include for directories.
    #[arg(long)]
    ext: Option<String>,

    /// Write results here and leave the originals untouched.
    #[arg(long)]
    output_dir: Option<String>,

    /// Scratch directory for temporary files.
    #[arg(long)]
    temp_dir: Option<String>,

    /// Do not keep a copy of the original (this is the default).
    #[arg(long)]
    no_backup: bool,

    /// Keep a '.velox-orig' backup of the original.
    #[arg(long)]
    keep_original: bool,

    /// Only run these plugin keys (comma separated).
    #[arg(long)]
    only: Option<String>,

    /// Skip these plugin keys (comma separated).
    #[arg(long)]
    skip: Option<String>,

    /// Video bitrate mode for this run.
    #[arg(long, value_parser = ["tier", "size", "quality"])]
    mode: Option<String>,

    /// Target output size in MiB for this run (implies --mode size).
    #[arg(long)]
    target_size_mb: Option<f64>,

    /// Bits per pixel for tier mode (implies --mode tier).
    #[arg(long)]
    bpp: Option<f64>,

    /// Process this many files in parallel.
    #[arg(short = 'j', long)]
    jobs: Option<usize>,

    /// Write a report next to each file.
    #[arg(long, action = ArgAction::SetTrue)]
    report: bool,

    /// Do not write a report.
    #[arg(long, action = ArgAction::SetTrue)]
    no_report: bool,

    /// Path to the ffmpeg binary.
    #[arg(long)]
    ffmpeg: Option<String>,

    /// Path to the ffprobe binary.
    #[arg(long)]
    ffprobe: Option<String>,

    /// Write a default config file to PATH and exit.
    #[arg(long, value_name = "PATH")]
    init_config: Option<PathBuf>,

    /// List the processing steps and exit.
    #[arg(long)]
    list_plugins: bool,

    /// Increase log verbosity.
    #[arg(short = 'v', long, action = ArgAction::Count)]
    verbose: u8,

    /// Only warnings and errors.
    #[arg(short = 'q', long)]
    quiet: bool,
}

struct CliSink;

impl EventSink for CliSink {
    fn emit(&self, event: PipelineEvent) {
        if let PipelineEvent::PassFinished { pass, .. } = event {
            tracing::info!("   \u{2713} {}", pass.describe());
        }
    }
}

fn main() -> std::process::ExitCode {
    let args = Args::parse();
    let runtime = match tokio::runtime::Runtime::new() {
        Ok(runtime) => runtime,
        Err(error) => {
            eprintln!("could not start async runtime: {error}");
            return std::process::ExitCode::from(2);
        }
    };
    let code = runtime.block_on(run(args));
    std::process::ExitCode::from(code)
}

async fn run(args: Args) -> u8 {
    if args.list_plugins {
        for key in DEFAULT_ORDER {
            println!("{key:<22} {}", plugin_display_name(key));
        }
        return 0;
    }

    if let Some(path) = &args.init_config {
        let config = Config::default();
        if let Err(error) = config.save(path) {
            eprintln!("{error}");
            return 2;
        }
        println!("Wrote default config to {}", path.display());
        return 0;
    }

    setup_logging(&args);

    let mut config = match &args.config {
        Some(path) => match Config::load(path) {
            Ok(config) => config,
            Err(error) => {
                tracing::error!("Could not load config: {error}");
                return 2;
            }
        },
        None => Config::default(),
    };

    apply_overrides(&mut config, &args);

    if args.paths.is_empty() {
        eprintln!("No paths given. Use --help for usage.");
        return 1;
    }

    if let (Some(only), Some(skip)) = (&args.only, &args.skip) {
        if !only.is_empty() && !skip.is_empty() {
            tracing::warn!("Both --only and --skip given; --skip wins for overlapping keys.");
        }
    }
    apply_plugin_selection(&mut config, args.only.as_deref(), args.skip.as_deref());

    let extensions: Vec<String> = args
        .ext
        .as_deref()
        .map(|value| {
            value
                .split(',')
                .map(|item| item.trim().trim_start_matches('.').to_lowercase())
                .filter(|item| !item.is_empty())
                .collect()
        })
        .unwrap_or_else(|| {
            DEFAULT_EXTENSIONS
                .iter()
                .map(|value| (*value).to_string())
                .collect()
        });

    let files = gather_files(&args.paths, args.recursive, &extensions);

    let ffmpeg = match Ffmpeg::resolve(&config.general.ffmpeg_path, &config.general.ffprobe_path) {
        Ok(ffmpeg) => Arc::new(ffmpeg),
        Err(error) => {
            tracing::error!("{error}");
            return 2;
        }
    };

    if args.info {
        let mut code = 0;
        for path in files {
            match ffmpeg.probe(&path).await {
                Ok(media) => {
                    println!("\n{}", path.display());
                    println!("  {}", media.summary());
                    println!("{}", media.stream_table());
                }
                Err(error) => {
                    eprintln!("{}: {error}", path.display());
                    code = 1;
                }
            }
        }
        return code;
    }

    if files.is_empty() {
        tracing::warn!("No matching files found.");
        return 1;
    }

    let pipeline = Arc::new(Pipeline::new(config.clone(), ffmpeg));
    let names = pipeline.enabled_plugin_names();
    tracing::info!("Velox {VERSION} - pipeline: {}", names.join(" \u{2192} "));
    if args.dry_run {
        tracing::info!("Dry run: no files will be modified.");
    }

    let jobs = config.general.concurrency.max(1);
    let workers = jobs.min(files.len());
    tracing::info!("Files to process: {} ({workers} at a time)", files.len());

    let cancel = CancellationToken::new();
    let signal_token = cancel.clone();
    tokio::spawn(async move {
        if tokio::signal::ctrl_c().await.is_ok() {
            tracing::warn!("Stopping after the current pass...");
            signal_token.cancel();
        }
    });

    let sink: Arc<dyn EventSink> = Arc::new(CliSink);
    let semaphore = Arc::new(Semaphore::new(workers));
    let mut set = JoinSet::new();

    for (index, file) in files.iter().cloned().enumerate() {
        let permit = match semaphore.clone().acquire_owned().await {
            Ok(permit) => permit,
            Err(_) => break,
        };
        let pipeline = pipeline.clone();
        let sink = sink.clone();
        let cancel = cancel.clone();
        let options = ProcessOptions {
            dry_run: args.dry_run,
            cancel,
        };
        set.spawn(async move {
            let _permit = permit;
            let report = pipeline.process(&file, &options, sink).await;
            (index, report)
        });
    }

    let mut results: Vec<Option<RunReport>> = (0..files.len()).map(|_| None).collect();
    while let Some(joined) = set.join_next().await {
        match joined {
            Ok((index, report)) => results[index] = Some(report),
            Err(error) => tracing::error!("worker failed: {error}"),
        }
    }

    let reports: Vec<RunReport> = results.into_iter().flatten().collect();
    let modified = reports
        .iter()
        .filter(|report| report.ok() && report.modified)
        .count();
    let skipped = reports.iter().filter(|report| report.skipped).count();
    let failed: Vec<&RunReport> = reports.iter().filter(|report| !report.ok()).collect();

    let total_original: u64 = reports
        .iter()
        .filter(|report| report.modified)
        .map(|report| report.original_bytes)
        .sum();
    let total_final: u64 = reports
        .iter()
        .filter(|report| report.modified)
        .map(|report| report.final_bytes)
        .sum();
    if total_original > 0 {
        let saved = total_original as i64 - total_final as i64;
        let percent = saved as f64 / total_original as f64 * 100.0;
        tracing::info!(
            "Space: {} \u{2192} {} (saved {}, {percent:.1}%)",
            fmt_bytes(total_original),
            fmt_bytes(total_final),
            fmt_bytes(saved.unsigned_abs())
        );
    }
    tracing::info!(
        "Done. {} processed, {} modified, {} skipped, {} failed.",
        reports.len(),
        modified,
        skipped,
        failed.len()
    );
    for report in &failed {
        tracing::error!(
            "  \u{2717} {}: {}",
            report
                .source
                .file_name()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default(),
            report.error.clone().unwrap_or_default()
        );
    }

    if failed.is_empty() {
        0
    } else {
        1
    }
}

fn setup_logging(args: &Args) {
    let level = if args.quiet {
        LevelFilter::WARN
    } else if args.verbose > 0 {
        LevelFilter::DEBUG
    } else {
        LevelFilter::INFO
    };
    let _ = tracing_subscriber::fmt()
        .with_max_level(level)
        .with_target(false)
        .without_time()
        .with_ansi(std::io::stderr().is_terminal())
        .with_writer(std::io::stderr)
        .try_init();
}

fn apply_overrides(config: &mut Config, args: &Args) {
    if let Some(value) = &args.output_dir {
        config.general.output_dir = value.clone();
    }
    if let Some(value) = &args.temp_dir {
        config.general.temp_dir = value.clone();
    }
    if args.no_backup {
        config.general.keep_original = false;
    }
    if args.keep_original {
        config.general.keep_original = true;
    }
    if let Some(value) = &args.ffmpeg {
        config.general.ffmpeg_path = value.clone();
    }
    if let Some(value) = &args.ffprobe {
        config.general.ffprobe_path = value.clone();
    }
    if let Some(value) = &args.mode {
        config.plugins.hevc_gpu.bitrate_mode = value.clone();
    }
    if let Some(value) = args.target_size_mb {
        config.plugins.hevc_gpu.target_size_mb = value;
        config.plugins.hevc_gpu.bitrate_mode = "size".to_string();
    }
    if let Some(value) = args.bpp {
        config.plugins.hevc_gpu.bits_per_pixel = value;
        config.plugins.hevc_gpu.bitrate_mode = "tier".to_string();
    }
    if let Some(value) = args.jobs {
        config.general.concurrency = value.max(1);
    }
    if args.report {
        config.general.write_report = true;
    }
    if args.no_report {
        config.general.write_report = false;
    }
}

fn apply_plugin_selection(config: &mut Config, only: Option<&str>, skip: Option<&str>) {
    if let Some(only) = only {
        let wanted: HashSet<&str> = only
            .split(',')
            .map(|value| value.trim())
            .filter(|value| !value.is_empty())
            .collect();
        for key in DEFAULT_ORDER {
            config.set_plugin_enabled(key, wanted.contains(&key));
        }
    }
    if let Some(skip) = skip {
        for key in skip.split(',').map(|value| value.trim()) {
            if !key.is_empty() {
                config.set_plugin_enabled(key, false);
            }
        }
    }
}

fn gather_files(paths: &[String], recursive: bool, extensions: &[String]) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let mut seen = HashSet::new();

    for raw in paths {
        let path = PathBuf::from(raw.replace('~', &home_dir_string()));
        if path.is_dir() {
            collect_dir(&path, recursive, extensions, &mut files, &mut seen);
        } else if path.is_file() {
            push_unique(path, &mut files, &mut seen);
        } else {
            tracing::warn!("Path does not exist, skipping: {}", path.display());
        }
    }
    files
}

fn collect_dir(
    dir: &Path,
    recursive: bool,
    extensions: &[String],
    files: &mut Vec<PathBuf>,
    seen: &mut HashSet<PathBuf>,
) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut entries: Vec<_> = entries.flatten().collect();
    entries.sort_by_key(|entry| entry.path());
    for entry in entries {
        let path = entry.path();
        if path.is_dir() {
            if recursive {
                collect_dir(&path, recursive, extensions, files, seen);
            }
            continue;
        }
        if !path.is_file() {
            continue;
        }
        let extension = path
            .extension()
            .map(|value| value.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if extensions.iter().any(|value| value == &extension) {
            push_unique(path, files, seen);
        }
    }
}

fn push_unique(path: PathBuf, files: &mut Vec<PathBuf>, seen: &mut HashSet<PathBuf>) {
    let canonical = path.canonicalize().unwrap_or_else(|_| path.clone());
    if seen.insert(canonical) {
        files.push(path);
    }
}

fn home_dir_string() -> String {
    std::env::var_os("HOME")
        .map(|value| value.to_string_lossy().into_owned())
        .unwrap_or_default()
}
