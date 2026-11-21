//! The Velox pipeline engine.
//!
//! Consecutive stream-copy (remux) plugins are merged into a single ffmpeg
//! pass; plugins that need the real file (video encode, size check) flush the
//! pending remux first.  All work happens in a scratch directory next to the
//! source and the result is only moved into place after the whole pipeline
//! succeeded.

use std::collections::{BTreeMap, HashMap};
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use serde::{Deserialize, Serialize};
use tempfile::TempDir;
use tokio_util::sync::CancellationToken;

use crate::config::Config;
use crate::error::{Result, VeloxError};
use crate::events::{EventSink, PassEvent, PipelineEvent};
use crate::ffmpeg::FfmpegBackend;
use crate::media::{stream_type, MediaInfo};
use crate::plugins::{build_plugins, PipelineContext, Plugin, PluginResult, RemuxPlan};
use crate::report::write_report;

const TYPE_SPEC: [(&str, &str); 5] = [
    ("video", "v"),
    ("audio", "a"),
    ("subtitle", "s"),
    ("data", "d"),
    ("attachment", "t"),
];

#[derive(Debug, Clone)]
pub struct ProcessOptions {
    pub dry_run: bool,
    pub cancel: CancellationToken,
}

impl Default for ProcessOptions {
    fn default() -> Self {
        Self {
            dry_run: false,
            cancel: CancellationToken::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PluginOutcome {
    pub key: String,
    pub name: String,
    pub result: PluginResult,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub command: Option<Vec<String>>,
    pub executed: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunReport {
    pub source: PathBuf,
    pub final_path: Option<PathBuf>,
    pub modified: bool,
    pub skipped: bool,
    pub cancelled: bool,
    pub reason: String,
    pub error: Option<String>,
    pub original_bytes: u64,
    pub final_bytes: u64,
    pub elapsed: f64,
    pub original_media: Option<MediaInfo>,
    pub final_media: Option<MediaInfo>,
    pub report_path: Option<PathBuf>,
    pub output_dir: Option<String>,
    pub kept_original: bool,
    pub passes: Vec<PassEvent>,
    pub outcomes: Vec<PluginOutcome>,
}

impl RunReport {
    fn new(source: PathBuf) -> Self {
        Self {
            source,
            final_path: None,
            modified: false,
            skipped: false,
            cancelled: false,
            reason: String::new(),
            error: None,
            original_bytes: 0,
            final_bytes: 0,
            elapsed: 0.0,
            original_media: None,
            final_media: None,
            report_path: None,
            output_dir: None,
            kept_original: false,
            passes: Vec::new(),
            outcomes: Vec::new(),
        }
    }

    pub fn ok(&self) -> bool {
        self.error.is_none() && !self.cancelled
    }

    pub fn saved_bytes(&self) -> i64 {
        if !self.modified || self.final_bytes == 0 {
            return 0;
        }
        self.original_bytes as i64 - self.final_bytes as i64
    }

    pub fn saved_percent(&self) -> f64 {
        if self.original_bytes == 0 {
            return 0.0;
        }
        self.saved_bytes() as f64 / self.original_bytes as f64 * 100.0
    }
}

pub struct Pipeline {
    config: Config,
    ffmpeg: Arc<dyn FfmpegBackend>,
    plugins: Vec<Box<dyn Plugin>>,
}

impl Pipeline {
    pub fn new(config: Config, ffmpeg: Arc<dyn FfmpegBackend>) -> Self {
        let plugins = build_plugins(&config);
        Self {
            config,
            ffmpeg,
            plugins,
        }
    }

    pub fn config(&self) -> &Config {
        &self.config
    }

    pub fn ffmpeg(&self) -> &Arc<dyn FfmpegBackend> {
        &self.ffmpeg
    }

    pub fn enabled_plugin_names(&self) -> Vec<&'static str> {
        self.plugins
            .iter()
            .filter(|plugin| plugin.enabled())
            .map(|plugin| plugin.name())
            .collect()
    }

    pub async fn probe(&self, path: &Path) -> Result<MediaInfo> {
        self.ffmpeg.probe(path).await
    }

    pub async fn process(
        &self,
        path: &Path,
        options: &ProcessOptions,
        sink: Arc<dyn EventSink>,
    ) -> RunReport {
        let source = path.to_path_buf();
        let mut report = RunReport::new(source.clone());
        let started = Instant::now();

        let original = match self.ffmpeg.probe(&source).await {
            Ok(media) => media,
            Err(error) => {
                report.error = Some(error.to_string());
                sink.emit(PipelineEvent::Error {
                    path: Some(source.clone()),
                    message: error.to_string(),
                });
                report.elapsed = started.elapsed().as_secs_f64();
                sink.emit(PipelineEvent::FileFinished {
                    report: Box::new(report.clone()),
                });
                return report;
            }
        };

        report.original_bytes = original.size;
        report.original_media = Some(original.clone());

        if !original.is_video() && !self.config.general.process_non_video {
            report.skipped = true;
            report.reason = "not a video file".to_string();
            report.final_path = Some(source.clone());
            report.final_bytes = original.size;
            report.elapsed = started.elapsed().as_secs_f64();
            sink.emit(PipelineEvent::FileFinished {
                report: Box::new(report.clone()),
            });
            return report;
        }

        tracing::info!("Processing {} ({})", source.display(), original.summary());
        sink.emit(PipelineEvent::FileStarted {
            path: source.clone(),
            summary: original.summary(),
        });

        let tmpdir = if options.dry_run {
            None
        } else {
            match self.scratch_dir(&source) {
                Ok(dir) => {
                    tracing::info!("Scratch directory: {}", dir.path().display());
                    if !same_filesystem(dir.path(), &source) {
                        tracing::warn!(
                            "Scratch directory is on a different volume than the source; the finished file will be copied instead of renamed."
                        );
                    }
                    Some(dir)
                }
                Err(error) => {
                    report.error = Some(error.to_string());
                    report.elapsed = started.elapsed().as_secs_f64();
                    sink.emit(PipelineEvent::Error {
                        path: Some(source.clone()),
                        message: error.to_string(),
                    });
                    sink.emit(PipelineEvent::FileFinished {
                        report: Box::new(report.clone()),
                    });
                    return report;
                }
            }
        };

        let ctx = PipelineContext::new(self.ffmpeg.clone(), original.clone(), options.dry_run);
        let mut state = RunState {
            pipeline: self,
            source: source.clone(),
            sink: sink.clone(),
            ctx,
            tmpdir,
            current: source.clone(),
            modified: false,
            would_change: false,
            pass_index: 0,
            pending: RemuxPlan::default(),
            group_outcomes: Vec::new(),
            virtual_media: None,
            probe_cache: HashMap::new(),
            outcomes: Vec::new(),
            passes: Vec::new(),
            cancel: options.cancel.clone(),
        };

        let run_succeeded = match state.run().await {
            Ok(()) => true,
            Err(VeloxError::Cancelled) => {
                report.cancelled = true;
                report.error = Some("Processing stopped by user".to_string());
                tracing::warn!("{}: processing stopped by user", source.display());
                false
            }
            Err(error) => {
                report.error = Some(error.to_string());
                tracing::error!("{}: {error}", source.display());
                sink.emit(PipelineEvent::Error {
                    path: Some(source.clone()),
                    message: error.to_string(),
                });
                false
            }
        };

        report.passes = state.passes;
        report.outcomes = state.outcomes;
        report.modified = run_succeeded && state.modified;
        let would_change = state.would_change;
        let current = state.current.clone();

        if report.error.is_none() && report.modified && !options.dry_run {
            let verification = self
                .verify_output(
                    &current,
                    &original,
                    self.expected_video_codec(&report),
                    sink.as_ref(),
                )
                .await;
            if let Err(problem) = verification {
                report.error = Some(format!("Output verification failed: {problem}"));
                tracing::error!(
                    "{}: output verification failed: {problem}",
                    source.display()
                );
                sink.emit(PipelineEvent::Error {
                    path: Some(source.clone()),
                    message: problem,
                });
            } else {
                match self.commit(&current, &source) {
                    Ok(final_path) => {
                        report.final_path = Some(final_path.clone());
                        report.output_dir = self
                            .config
                            .general
                            .output_dir_path()
                            .map(|path| path.to_string_lossy().into_owned());
                        report.kept_original =
                            report.output_dir.is_some() || self.config.general.keep_original;
                        if !self.config.general.keep_original && report.output_dir.is_none() {
                            self.remove_backup(&source);
                        }
                        tracing::info!("Wrote {}", final_path.display());
                    }
                    Err(error) => {
                        report.error = Some(error.to_string());
                        tracing::error!("{}: {error}", source.display());
                    }
                }
            }
        }

        if report.final_path.is_none() && report.error.is_none() {
            report.final_path = Some(if report.modified {
                current
            } else {
                source.clone()
            });
        }

        if let Some(final_path) = &report.final_path {
            report.final_bytes = file_size(final_path);
            if report.modified && !options.dry_run {
                report.final_media = self.ffmpeg.probe(final_path).await.ok();
            }
        }

        if options.dry_run && would_change {
            tracing::info!("(dry-run) no files were written");
        }

        if report.modified {
            self.maybe_write_report(&mut report);
        }

        report.elapsed = started.elapsed().as_secs_f64();
        sink.emit(PipelineEvent::FileFinished {
            report: Box::new(report.clone()),
        });
        report
    }

    fn scratch_dir(&self, source: &Path) -> Result<TempDir> {
        let configured = self.config.general.temp_dir.trim();
        let parent = if configured.is_empty()
            || ["source", "auto", "same"]
                .iter()
                .any(|value| configured.eq_ignore_ascii_case(value))
        {
            source
                .parent()
                .map(Path::to_path_buf)
                .unwrap_or_else(|| PathBuf::from("."))
        } else {
            let mut value = configured.to_string();
            let source_dir = source
                .parent()
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or_default();
            let source_name = source
                .file_name()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default();
            value = value.replace("{source_dir}", &source_dir);
            value = value.replace("{source_name}", &source_name);
            if let Some(rest) = value.strip_prefix("~/") {
                if let Some(home) = std::env::var_os("HOME") {
                    value = PathBuf::from(home)
                        .join(rest)
                        .to_string_lossy()
                        .into_owned();
                }
            }
            PathBuf::from(value)
        };
        std::fs::create_dir_all(&parent)?;
        tempfile::Builder::new()
            .prefix(".velox-")
            .tempdir_in(&parent)
            .map_err(VeloxError::Io)
    }

    fn expected_video_codec(&self, report: &RunReport) -> Option<String> {
        report
            .outcomes
            .iter()
            .find(|outcome| outcome.key == "hevc_gpu" && outcome.executed)
            .map(|_| self.config.plugins.hevc_gpu.target_codec.clone())
    }

    async fn verify_output(
        &self,
        path: &Path,
        original: &MediaInfo,
        expected_codec: Option<String>,
        sink: &dyn EventSink,
    ) -> std::result::Result<(), String> {
        sink.emit(PipelineEvent::StepNote {
            path: original.path.clone(),
            message: "Verifying output...".to_string(),
        });
        let media = self
            .ffmpeg
            .probe(path)
            .await
            .map_err(|error| error.to_string())?;

        if original.is_video() && !media.is_video() {
            return Err("no video stream in the output".to_string());
        }

        if original.duration() > 0.0 && media.duration() > 0.0 {
            let tolerance = 1.0f64.max(original.duration() * 0.005);
            if (media.duration() - original.duration()).abs() > tolerance {
                return Err(format!(
                    "duration mismatch ({:.1}s vs original {:.1}s)",
                    media.duration(),
                    original.duration()
                ));
            }
        }

        if let Some(expected) = expected_codec {
            if let Some(stream) = media.video_stream() {
                let codec = stream.codec();
                if !codec.is_empty() && codec != expected.to_lowercase() {
                    return Err(format!("video codec is {codec}, expected {expected}"));
                }
            }
        }

        if self.config.general.verify_full_decode {
            let path_string = path.to_string_lossy();
            let args: Vec<OsString> = [
                "-v",
                "error",
                "-xerror",
                "-i",
                path_string.as_ref(),
                "-f",
                "null",
                "-",
            ]
            .into_iter()
            .map(OsString::from)
            .collect();
            self.ffmpeg
                .run(&args, 0.0, None, None)
                .await
                .map_err(|error| format!("decoding reported errors: {error}"))?;
        }

        tracing::info!("Output verified");
        Ok(())
    }

    fn maybe_write_report(&self, report: &mut RunReport) {
        if !self.config.general.write_report {
            return;
        }
        if !(report.modified || report.error.is_some() || report.cancelled) {
            return;
        }
        match write_report(report, &self.config) {
            Ok(path) => {
                tracing::info!("Report: {}", path.display());
                report.report_path = Some(path);
            }
            Err(error) => tracing::warn!("Could not write report: {error}"),
        }
    }

    fn commit(&self, current: &Path, source: &Path) -> Result<PathBuf> {
        let general = &self.config.general;
        let final_container = current
            .extension()
            .map(|value| value.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        let source_container = source
            .extension()
            .map(|value| value.to_string_lossy().to_lowercase())
            .unwrap_or_default();

        if let Some(output_dir) = general.output_dir_path() {
            std::fs::create_dir_all(&output_dir)?;
            let target = unique_path(&output_dir.join(format!(
                    "{}.{}",
                    source
                        .file_stem()
                        .map(|value| value.to_string_lossy().into_owned())
                        .unwrap_or_default(),
                    final_container
                )));
            move_file(current, &target)?;
            return Ok(target);
        }

        if final_container == source_container {
            if general.keep_original {
                let backup = source.with_file_name(format!(
                    "{}.velox-orig{}",
                    source
                        .file_stem()
                        .map(|value| value.to_string_lossy().into_owned())
                        .unwrap_or_default(),
                    source
                        .extension()
                        .map(|value| format!(".{}", value.to_string_lossy()))
                        .unwrap_or_default()
                ));
                std::fs::copy(source, backup)?;
            }
            move_file(current, source)?;
            return Ok(source.to_path_buf());
        }

        let target = unique_path(&source.with_extension(&final_container));
        move_file(current, &target)?;
        if !general.keep_original {
            let _ = std::fs::remove_file(source);
        }
        Ok(target)
    }

    fn remove_backup(&self, source: &Path) {
        let backup = source.with_file_name(format!(
            "{}.velox-orig{}",
            source
                .file_stem()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default(),
            source
                .extension()
                .map(|value| format!(".{}", value.to_string_lossy()))
                .unwrap_or_default()
        ));
        let _ = std::fs::remove_file(backup);
    }
}

struct RunState<'a> {
    pipeline: &'a Pipeline,
    source: PathBuf,
    sink: Arc<dyn EventSink>,
    ctx: PipelineContext,
    tmpdir: Option<TempDir>,
    current: PathBuf,
    modified: bool,
    would_change: bool,
    pass_index: usize,
    pending: RemuxPlan,
    group_outcomes: Vec<usize>,
    virtual_media: Option<MediaInfo>,
    probe_cache: HashMap<PathBuf, MediaInfo>,
    outcomes: Vec<PluginOutcome>,
    passes: Vec<PassEvent>,
    cancel: CancellationToken,
}

impl<'a> RunState<'a> {
    async fn probe(&mut self, path: &Path) -> Result<MediaInfo> {
        if let Some(media) = self.probe_cache.get(path) {
            return Ok(media.clone());
        }
        let media = self.pipeline.ffmpeg.probe(path).await?;
        self.probe_cache.insert(path.to_path_buf(), media.clone());
        Ok(media)
    }

    async fn run(&mut self) -> Result<()> {
        let count = self.pipeline.plugins.len();
        for index in 0..count {
            if self.cancel.is_cancelled() {
                return Err(VeloxError::Cancelled);
            }

            let (key, name, mergeable, enabled) = {
                let plugin = &self.pipeline.plugins[index];
                (
                    plugin.key(),
                    plugin.name(),
                    plugin.mergeable(),
                    plugin.enabled(),
                )
            };
            if !enabled {
                tracing::debug!("Plugin {key} disabled, skipping");
                continue;
            }

            if !mergeable {
                self.flush().await?;
            }

            let current = self.current.clone();
            let mut media = if let Some(virtual_media) = &self.virtual_media {
                virtual_media.clone()
            } else {
                self.probe(&current).await?
            };
            if !self.pending.drop.is_empty() {
                media = media.without_streams(&self.pending.drop);
            }

            self.sink.emit(PipelineEvent::StepStarted {
                path: self.source.clone(),
                key: key.to_string(),
                name: name.to_string(),
            });

            let result = {
                let plugin = &self.pipeline.plugins[index];
                plugin.run(&media, &self.ctx).await?
            };

            tracing::info!("[{name}]");
            for line in &result.log {
                tracing::info!("   {line}");
                self.sink.emit(PipelineEvent::StepNote {
                    path: self.source.clone(),
                    message: line.clone(),
                });
            }

            let outcome_index = self.outcomes.len();
            self.outcomes.push(PluginOutcome {
                key: key.to_string(),
                name: name.to_string(),
                result: result.clone(),
                command: None,
                executed: false,
                output_path: None,
            });

            if let Some(error) = &result.error {
                return Err(VeloxError::plugin(error.clone()));
            }

            if !result.process_file {
                continue;
            }

            if let Some(remux) = &result.remux {
                self.pending.merge(remux);
                self.group_outcomes.push(outcome_index);
                continue;
            }

            self.flush().await?;

            let current = self.current.clone();
            let base = self.probe(&current).await?;
            let out_container = result
                .container
                .clone()
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| base.container());
            let output_path = self.scratch_output(&out_container);
            let command = build_command(
                &self.pipeline.ffmpeg.ffmpeg_path(),
                &result,
                &current,
                &output_path,
            );
            let full_display = command_display(&command);

            self.outcomes[outcome_index].command = Some(full_display.clone());

            self.sink.emit(PipelineEvent::PassStarted {
                path: self.source.clone(),
                description: name.to_string(),
            });
            tracing::info!("   -> ffmpeg: {}", display_args(&command));

            if self.ctx.dry_run {
                self.would_change = true;
                continue;
            }

            let input_bytes = file_size(&current);
            let duration = base.duration();
            let args: Vec<OsString> = command.into_iter().skip(1).collect();
            let source_path = self.source.clone();
            let sink = self.sink.clone();
            let progress = move |fraction: f64| {
                sink.emit(PipelineEvent::Progress {
                    path: source_path.clone(),
                    fraction,
                });
            };
            self.pipeline
                .ffmpeg
                .run(&args, duration, Some(&progress), Some(&self.cancel))
                .await?;
            let output_bytes = file_size(&output_path);
            self.outcomes[outcome_index].executed = true;
            self.outcomes[outcome_index].output_path = Some(output_path.clone());

            let event = PassEvent {
                steps: vec![name.to_string()],
                kind: if key == "hevc_gpu" {
                    "transcode".to_string()
                } else {
                    key.to_string()
                },
                input_bytes,
                output_bytes,
                command: full_display,
                plan: None,
            };
            self.passes.push(event.clone());
            self.sink.emit(PipelineEvent::PassFinished {
                path: self.source.clone(),
                pass: Box::new(event),
            });
            self.current = output_path;
            self.modified = true;
            self.pass_index += 1;
            self.probe_cache.clear();
            self.virtual_media = None;
        }

        self.flush().await
    }

    async fn flush(&mut self) -> Result<()> {
        if self.pending.is_empty() {
            self.group_outcomes.clear();
            return Ok(());
        }
        if self.cancel.is_cancelled() {
            return Err(VeloxError::Cancelled);
        }

        let plan = std::mem::take(&mut self.pending);
        let outcomes = std::mem::take(&mut self.group_outcomes);
        let current = self.current.clone();
        let base = self.probe(&current).await?;
        let args = build_remux_args(&plan, &base);
        if args.is_empty() {
            return Ok(());
        }

        let output_path = self.scratch_output(&base.container());
        let mut command: Vec<OsString> = vec![
            self.pipeline.ffmpeg.ffmpeg_path().into_os_string(),
            OsString::from("-y"),
            OsString::from("-hide_banner"),
            OsString::from("-loglevel"),
            OsString::from("error"),
            OsString::from("-i"),
            current.as_os_str().to_os_string(),
        ];
        command.extend(args.iter().map(OsString::from));
        command.push(output_path.as_os_str().to_os_string());

        let steps: Vec<String> = outcomes
            .iter()
            .map(|index| self.outcomes[*index].name.clone())
            .collect();
        self.sink.emit(PipelineEvent::PassStarted {
            path: self.source.clone(),
            description: steps.join(" + "),
        });
        tracing::info!("   -> ffmpeg (merged remux): {}", display_args(&command));
        let full_display = command_display(&command);
        for index in &outcomes {
            self.outcomes[*index].command = Some(full_display.clone());
        }

        if self.ctx.dry_run {
            self.virtual_media = Some(apply_plan_to_media(&base, &plan));
            self.would_change = true;
            return Ok(());
        }

        let input_bytes = file_size(&current);
        let duration = base.duration();
        let ffmpeg_args: Vec<OsString> = command.iter().skip(1).cloned().collect();
        let source_path = self.source.clone();
        let sink = self.sink.clone();
        let progress = move |fraction: f64| {
            sink.emit(PipelineEvent::Progress {
                path: source_path.clone(),
                fraction,
            });
        };
        self.pipeline
            .ffmpeg
            .run(&ffmpeg_args, duration, Some(&progress), Some(&self.cancel))
            .await?;
        let output_bytes = file_size(&output_path);
        for index in &outcomes {
            self.outcomes[*index].executed = true;
            self.outcomes[*index].output_path = Some(output_path.clone());
        }

        let event = PassEvent {
            steps: steps.clone(),
            kind: "remux".to_string(),
            input_bytes,
            output_bytes,
            command: full_display,
            plan: Some(plan),
        };
        self.passes.push(event.clone());
        self.sink.emit(PipelineEvent::PassFinished {
            path: self.source.clone(),
            pass: Box::new(event),
        });
        self.current = output_path;
        self.modified = true;
        self.pass_index += 1;
        self.probe_cache.clear();
        self.virtual_media = None;
        Ok(())
    }

    fn scratch_output(&self, container: &str) -> PathBuf {
        if self.ctx.dry_run {
            return PathBuf::from("<output>");
        }
        let dir = self
            .tmpdir
            .as_ref()
            .map(|dir| dir.path().to_path_buf())
            .unwrap_or_else(|| PathBuf::from("."));
        dir.join(format!(
            "{:02}_{}.{}",
            self.pass_index,
            self.source
                .file_stem()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default(),
            container
        ))
    }
}

fn display_args(command: &[OsString]) -> String {
    command
        .iter()
        .map(|value| value.to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join(" ")
}

fn command_display(args: &[OsString]) -> Vec<String> {
    args.iter()
        .map(|value| value.to_string_lossy().into_owned())
        .collect()
}

fn build_command(
    ffmpeg: &Path,
    result: &PluginResult,
    input: &Path,
    output: &Path,
) -> Vec<OsString> {
    let mut command: Vec<OsString> = vec![
        ffmpeg.as_os_str().to_os_string(),
        OsString::from("-y"),
        OsString::from("-hide_banner"),
        OsString::from("-loglevel"),
        OsString::from("error"),
    ];
    command.extend(result.input_args.iter().map(OsString::from));
    command.push(OsString::from("-i"));
    command.push(input.as_os_str().to_os_string());
    command.extend(result.output_args.iter().map(OsString::from));
    command.push(output.as_os_str().to_os_string());
    command
}

pub fn build_remux_args(plan: &RemuxPlan, media: &MediaInfo) -> Vec<String> {
    let input_indices: Vec<usize> = media.streams.iter().map(|stream| stream.index).collect();
    let final_order = final_order(plan, &input_indices);
    if final_order.is_empty() {
        return Vec::new();
    }

    let unchanged = plan.drop.is_empty()
        && plan.codecs.is_empty()
        && plan.channels.is_empty()
        && plan.bitrates.is_empty()
        && plan.metadata.is_empty()
        && plan.dispositions.is_empty()
        && plan
            .order
            .as_ref()
            .map(|order| order == &input_indices)
            .unwrap_or(true);
    if unchanged {
        return Vec::new();
    }

    let by_index: BTreeMap<usize, &crate::media::Stream> = media
        .streams
        .iter()
        .map(|stream| (stream.index, stream))
        .collect();
    let mut args = Vec::new();
    for index in &final_order {
        args.extend(["-map".to_string(), format!("0:{index}")]);
    }
    args.extend(["-c".to_string(), "copy".to_string()]);

    let mut counter: BTreeMap<&str, usize> = BTreeMap::new();
    let mut out_index: BTreeMap<usize, usize> = BTreeMap::new();
    for index in &final_order {
        let kind = by_index
            .get(index)
            .map(|stream| stream_type(stream))
            .unwrap_or("");
        let position = counter.entry(kind).or_insert(0);
        out_index.insert(*index, *position);
        *position += 1;
    }

    for (index, codec) in &plan.codecs {
        if let Some(position) = out_index.get(index) {
            let spec = spec_for(by_index.get(index).copied());
            args.extend([format!("-c:{spec}:{position}"), codec.clone()]);
        }
    }
    for (index, count) in &plan.channels {
        if let Some(position) = out_index.get(index) {
            let spec = spec_for(by_index.get(index).copied());
            args.extend([format!("-ac:{spec}:{position}"), count.to_string()]);
        }
    }
    for (index, bitrate) in &plan.bitrates {
        if let Some(position) = out_index.get(index) {
            let spec = spec_for(by_index.get(index).copied());
            args.extend([format!("-b:{spec}:{position}"), bitrate.clone()]);
        }
    }
    for (index, items) in &plan.metadata {
        let Some(position) = out_index.get(index) else {
            continue;
        };
        let spec = spec_for(by_index.get(index).copied());
        for (key, value) in items {
            args.extend([
                format!("-metadata:s:{spec}:{position}"),
                format!("{key}={value}"),
            ]);
        }
    }
    for (index, value) in &plan.dispositions {
        if let Some(position) = out_index.get(index) {
            let spec = spec_for(by_index.get(index).copied());
            args.extend([format!("-disposition:{spec}:{position}"), value.clone()]);
        }
    }

    args.extend(["-max_muxing_queue_size".to_string(), "9999".to_string()]);
    args
}

fn spec_for(stream: Option<&crate::media::Stream>) -> &'static str {
    let Some(stream) = stream else {
        return "a";
    };
    TYPE_SPEC
        .iter()
        .find(|(kind, _)| kind == &stream_type(stream))
        .map(|(_, spec)| *spec)
        .unwrap_or("a")
}

fn final_order(plan: &RemuxPlan, input_indices: &[usize]) -> Vec<usize> {
    if let Some(order) = &plan.order {
        return order
            .iter()
            .copied()
            .filter(|index| input_indices.contains(index) && !plan.drop.contains(index))
            .collect();
    }
    input_indices
        .iter()
        .copied()
        .filter(|index| !plan.drop.contains(index))
        .collect()
}

fn apply_plan_to_media(media: &MediaInfo, plan: &RemuxPlan) -> MediaInfo {
    let by_index: BTreeMap<usize, &crate::media::Stream> = media
        .streams
        .iter()
        .map(|stream| (stream.index, stream))
        .collect();
    let input_indices: Vec<usize> = media.streams.iter().map(|stream| stream.index).collect();
    let mut streams = Vec::new();
    for index in final_order(plan, &input_indices) {
        let Some(stream) = by_index.get(&index) else {
            continue;
        };
        let mut stream = (*stream).clone();
        if let Some(codec) = plan.codecs.get(&index) {
            stream.codec_name = Some(codec.clone());
        }
        if let Some(channels) = plan.channels.get(&index) {
            stream.channels = Some(*channels);
        }
        for (key, value) in plan.metadata.get(&index).into_iter().flatten() {
            stream
                .tags
                .insert(key.clone(), serde_json::Value::String(value.clone()));
        }
        streams.push(stream);
    }
    let mut copy = media.clone();
    copy.streams = streams;
    copy
}

fn file_size(path: &Path) -> u64 {
    std::fs::metadata(path).map(|meta| meta.len()).unwrap_or(0)
}

fn unique_path(path: &Path) -> PathBuf {
    if !path.exists() {
        return path.to_path_buf();
    }
    for number in 1..10_000 {
        let candidate = path.with_file_name(format!(
            "{} ({number}){}",
            path.file_stem()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default(),
            path.extension()
                .map(|value| format!(".{}", value.to_string_lossy()))
                .unwrap_or_default()
        ));
        if !candidate.exists() {
            return candidate;
        }
    }
    path.to_path_buf()
}

fn move_file(source: &Path, target: &Path) -> Result<()> {
    match std::fs::rename(source, target) {
        Ok(()) => Ok(()),
        Err(_) => {
            std::fs::copy(source, target)?;
            std::fs::remove_file(source)?;
            Ok(())
        }
    }
}

fn same_filesystem(a: &Path, b: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        match (std::fs::metadata(a), std::fs::metadata(b)) {
            (Ok(a), Ok(b)) => a.dev() == b.dev(),
            _ => false,
        }
    }
    #[cfg(not(unix))]
    {
        let _ = (a, b);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::NoopSink;
    use crate::ffmpeg::tests::FakeFfmpeg;
    use crate::media::{FormatInfo, ProbeData, Stream};

    fn media() -> MediaInfo {
        let streams = vec![
            Stream {
                index: 0,
                codec_type: "audio".into(),
                codec_name: Some("eac3".into()),
                channels: Some(6),
                ..Default::default()
            },
            Stream {
                index: 1,
                codec_type: "video".into(),
                codec_name: Some("h264".into()),
                ..Default::default()
            },
            Stream {
                index: 2,
                codec_type: "subtitle".into(),
                codec_name: Some("subrip".into()),
                ..Default::default()
            },
        ];
        MediaInfo::from_probe(
            "/tmp/test.mkv",
            ProbeData {
                streams,
                format: FormatInfo {
                    duration: Some("60".into()),
                    ..Default::default()
                },
            },
            100,
        )
    }

    #[test]
    fn builds_reorder_maps() {
        let plan = RemuxPlan {
            order: Some(vec![1, 0, 2]),
            ..Default::default()
        };
        let args = build_remux_args(&plan, &media());
        assert_eq!(
            args,
            vec![
                "-map",
                "0:1",
                "-map",
                "0:0",
                "-map",
                "0:2",
                "-c",
                "copy",
                "-max_muxing_queue_size",
                "9999"
            ]
        );
    }

    #[test]
    fn drops_and_metadata_use_output_positions() {
        let mut plan = RemuxPlan {
            drop: [0].into_iter().collect(),
            ..Default::default()
        };
        plan.metadata
            .insert(2, vec![("language".into(), "eng".into())]);
        let args = build_remux_args(&plan, &media());
        assert!(args.contains(&"-map".to_string()));
        assert!(args.contains(&"0:1".to_string()));
        assert!(args.contains(&"0:2".to_string()));
        assert!(!args.contains(&"0:0".to_string()));
        assert!(args
            .windows(2)
            .any(|pair| pair[0] == "-metadata:s:s:0" && pair[1] == "language=eng"));
    }

    #[test]
    fn empty_plan_produces_no_args() {
        assert!(build_remux_args(&RemuxPlan::default(), &media()).is_empty());
    }

    #[tokio::test]
    async fn skips_non_video_files() {
        let probe = ProbeData {
            streams: vec![],
            format: FormatInfo::default(),
        };
        let fake = Arc::new(FakeFfmpeg::new(probe));
        let pipeline = Pipeline::new(Config::default(), fake);
        let report = pipeline
            .process(
                Path::new("/tmp/audio.mp3"),
                &ProcessOptions {
                    dry_run: true,
                    ..Default::default()
                },
                Arc::new(NoopSink),
            )
            .await;
        assert!(report.skipped);
        assert_eq!(report.reason, "not a video file");
    }
}
