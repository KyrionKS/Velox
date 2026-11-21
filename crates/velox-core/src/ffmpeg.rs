//! `ffmpeg` / `ffprobe` wrappers: discovery, probing, execution, progress and
//! encoder capability checks.

use std::collections::HashMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Mutex;

use async_trait::async_trait;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};
use tokio::process::Command;
use tokio_util::sync::CancellationToken;

use crate::error::{display_command, missing_tool, Result, VeloxError};
use crate::media::{MediaInfo, ProbeData};

/// Common install locations, checked when PATH is not set (e.g. an app launched
/// from the Finder gets a minimal environment on macOS).
const FALLBACK_DIRS: [&str; 4] = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    "/usr/bin",
];

pub type ProgressSink = dyn Fn(f64) + Send + Sync;

#[async_trait]
pub trait FfmpegBackend: Send + Sync {
    fn ffmpeg_path(&self) -> PathBuf;
    fn ffprobe_path(&self) -> PathBuf;

    async fn probe(&self, path: &Path) -> Result<MediaInfo>;

    async fn run(
        &self,
        args: &[OsString],
        duration: f64,
        progress: Option<&ProgressSink>,
        cancel: Option<&CancellationToken>,
    ) -> Result<()>;

    async fn has_encoder(
        &self,
        encoder: &str,
        input_args: &[String],
        video_filter: Option<&str>,
    ) -> bool;

    async fn has_audio_encoder(&self, encoder: &str) -> bool;

    async fn encoders(&self) -> Vec<String>;
    async fn audio_encoders(&self) -> Vec<String>;

    async fn versions(&self) -> (String, String);
}

#[derive(Debug)]
pub struct Ffmpeg {
    ffmpeg_path: PathBuf,
    ffprobe_path: PathBuf,
    encoder_cache: Mutex<HashMap<String, bool>>,
    audio_encoder_cache: Mutex<HashMap<String, bool>>,
    encoders_cache: Mutex<Option<Vec<String>>>,
    audio_encoders_cache: Mutex<Option<Vec<String>>>,
}

impl Ffmpeg {
    pub fn new(ffmpeg_path: impl Into<PathBuf>, ffprobe_path: impl Into<PathBuf>) -> Self {
        Self {
            ffmpeg_path: ffmpeg_path.into(),
            ffprobe_path: ffprobe_path.into(),
            encoder_cache: Mutex::new(HashMap::new()),
            audio_encoder_cache: Mutex::new(HashMap::new()),
            encoders_cache: Mutex::new(None),
            audio_encoders_cache: Mutex::new(None),
        }
    }

    /// Resolve both binaries, failing fast with a helpful message.
    pub fn resolve(ffmpeg: &str, ffprobe: &str) -> Result<Self> {
        let ffmpeg_path = locate_tool(ffmpeg).ok_or_else(|| missing_tool("ffmpeg"))?;
        let ffprobe_path = locate_tool(ffprobe).ok_or_else(|| missing_tool("ffprobe"))?;
        Ok(Self::new(ffmpeg_path, ffprobe_path))
    }

    fn command(&self, args: &[OsString]) -> Command {
        let mut command = Command::new(&self.ffmpeg_path);
        command.args(args);
        command
    }
}

pub fn locate_tool(tool: &str) -> Option<PathBuf> {
    let candidate = PathBuf::from(tool);

    // An explicit path (absolute or containing a separator) wins.
    if candidate.components().count() > 1 || candidate.is_absolute() {
        if candidate.is_file() {
            return Some(candidate);
        }
        return None;
    }

    if let Some(path) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&path) {
            let full = dir.join(tool);
            if full.is_file() {
                return Some(full);
            }
            #[cfg(windows)]
            {
                let exe = dir.join(format!("{tool}.exe"));
                if exe.is_file() {
                    return Some(exe);
                }
            }
        }
    }

    for directory in FALLBACK_DIRS {
        let full = PathBuf::from(directory).join(tool);
        if full.is_file() {
            return Some(full);
        }
    }
    None
}

#[async_trait]
impl FfmpegBackend for Ffmpeg {
    fn ffmpeg_path(&self) -> PathBuf {
        self.ffmpeg_path.clone()
    }

    fn ffprobe_path(&self) -> PathBuf {
        self.ffprobe_path.clone()
    }

    async fn probe(&self, path: &Path) -> Result<MediaInfo> {
        let output = Command::new(&self.ffprobe_path)
            .args([
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
            ])
            .arg(path)
            .output()
            .await
            .map_err(|error| {
                VeloxError::ffmpeg(
                    format!("could not run ffprobe: {error}"),
                    vec![self.ffprobe_path.to_string_lossy().into_owned()],
                )
            })?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let tail = tail_lines(&stderr, 12);
            return Err(VeloxError::ffmpeg(
                format!(
                    "ffprobe failed for {}: {tail}",
                    path.file_name()
                        .map(|value| value.to_string_lossy().into_owned())
                        .unwrap_or_default()
                ),
                vec![
                    self.ffprobe_path.to_string_lossy().into_owned(),
                    path.to_string_lossy().into_owned(),
                ],
            ));
        }

        let probe: ProbeData = serde_json::from_slice(&output.stdout).map_err(|error| {
            VeloxError::ffmpeg(
                format!("could not parse ffprobe output: {error}"),
                vec![self.ffprobe_path.to_string_lossy().into_owned()],
            )
        })?;
        let size = std::fs::metadata(path).map(|meta| meta.len()).unwrap_or(0);
        Ok(MediaInfo::from_probe(path, probe, size))
    }

    async fn run(
        &self,
        args: &[OsString],
        duration: f64,
        progress: Option<&ProgressSink>,
        cancel: Option<&CancellationToken>,
    ) -> Result<()> {
        if progress.is_none() && cancel.is_none() {
            let output = self.command(args).output().await?;
            if !output.status.success() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                return Err(VeloxError::ffmpeg(
                    format!(
                        "ffmpeg failed (exit {}).\n{}",
                        output.status.code().unwrap_or(-1),
                        tail_lines(&stderr, 12)
                    ),
                    display_command(&self.ffmpeg_path, args),
                ));
            }
            return Ok(());
        }

        let mut command = Command::new(&self.ffmpeg_path);
        command
            .arg("-progress")
            .arg("pipe:1")
            .arg("-nostats")
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = command.spawn()?;
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr = child.stderr.take().expect("piped stderr");
        let mut lines = BufReader::new(stdout).lines();
        let stderr_task = tokio::spawn(async move {
            let mut buffer = Vec::new();
            let mut stderr = stderr;
            let _ = stderr.read_to_end(&mut buffer).await;
            String::from_utf8_lossy(&buffer).into_owned()
        });

        let mut cancelled = false;
        loop {
            tokio::select! {
                _ = wait_for_cancel(cancel) => {
                    cancelled = true;
                    let _ = child.start_kill();
                    break;
                }
                line = lines.next_line() => {
                    match line? {
                        Some(line) => {
                            if let Some(callback) = progress {
                                if let Some(fraction) = parse_progress_line(&line, duration) {
                                    callback(fraction);
                                }
                            }
                        }
                        None => break,
                    }
                }
            }
        }

        let status = child.wait().await?;
        let stderr = stderr_task.await.unwrap_or_default();

        if cancelled {
            return Err(VeloxError::Cancelled);
        }
        if !status.success() {
            return Err(VeloxError::ffmpeg(
                format!(
                    "ffmpeg failed (exit {}).\n{}",
                    status.code().unwrap_or(-1),
                    tail_lines(&stderr, 12)
                ),
                display_command(&self.ffmpeg_path, args),
            ));
        }
        if let Some(callback) = progress {
            callback(1.0);
        }
        Ok(())
    }

    async fn has_encoder(
        &self,
        encoder: &str,
        input_args: &[String],
        video_filter: Option<&str>,
    ) -> bool {
        let key = format!(
            "{encoder}|{}|{}",
            input_args.join(" "),
            video_filter.unwrap_or("")
        );
        if let Some(value) = self.encoder_cache.lock().unwrap().get(&key).copied() {
            return value;
        }

        let mut command = Command::new(&self.ffmpeg_path);
        command.args(["-hide_banner", "-loglevel", "error"]);
        command.args(input_args);
        command.args(["-f", "lavfi", "-i", "color=c=black:s=256x256:d=1:r=30"]);
        if let Some(filter) = video_filter {
            command.args(["-vf", filter]);
        }
        command.args(["-c:v", encoder, "-frames:v", "1", "-f", "null", "-"]);

        let ok = command
            .output()
            .await
            .map(|output| output.status.success())
            .unwrap_or(false);
        self.encoder_cache.lock().unwrap().insert(key, ok);
        ok
    }

    async fn has_audio_encoder(&self, encoder: &str) -> bool {
        if let Some(value) = self
            .audio_encoder_cache
            .lock()
            .unwrap()
            .get(encoder)
            .copied()
        {
            return value;
        }
        let available = self.audio_encoders().await;
        let ok = available.iter().any(|name| name == encoder);
        self.audio_encoder_cache
            .lock()
            .unwrap()
            .insert(encoder.to_string(), ok);
        ok
    }

    async fn encoders(&self) -> Vec<String> {
        if let Some(cached) = self.encoders_cache.lock().unwrap().clone() {
            return cached;
        }
        let names = list_encoders(&self.ffmpeg_path, None).await;
        *self.encoders_cache.lock().unwrap() = Some(names.clone());
        names
    }

    async fn audio_encoders(&self) -> Vec<String> {
        if let Some(cached) = self.audio_encoders_cache.lock().unwrap().clone() {
            return cached;
        }
        let names = list_encoders(&self.ffmpeg_path, Some('A')).await;
        *self.audio_encoders_cache.lock().unwrap() = Some(names.clone());
        names
    }

    async fn versions(&self) -> (String, String) {
        let ffmpeg = version_line(&self.ffmpeg_path).await;
        let ffprobe = version_line(&self.ffprobe_path).await;
        (ffmpeg, ffprobe)
    }
}

async fn wait_for_cancel(cancel: Option<&CancellationToken>) {
    match cancel {
        Some(token) => token.cancelled().await,
        None => std::future::pending::<()>().await,
    }
}

fn parse_progress_line(line: &str, duration: f64) -> Option<f64> {
    let (key, value) = line.split_once('=')?;
    let value = value.trim();
    let seconds = match key.trim() {
        "out_time_us" => {
            if value.is_empty() || value == "N/A" {
                return None;
            }
            value.parse::<f64>().ok()? / 1_000_000.0
        }
        "out_time" => parse_timecode(value)?,
        _ => return None,
    };
    if duration <= 0.0 {
        return None;
    }
    Some((seconds / duration).clamp(0.0, 1.0))
}

fn parse_timecode(value: &str) -> Option<f64> {
    let mut parts = value.split(':');
    let hours = parts.next()?.parse::<f64>().ok()?;
    let minutes = parts.next()?.parse::<f64>().ok()?;
    let seconds = parts.next()?.parse::<f64>().ok()?;
    Some(hours * 3600.0 + minutes * 60.0 + seconds)
}

fn tail_lines(text: &str, count: usize) -> String {
    let lines: Vec<&str> = text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .collect();
    let start = lines.len().saturating_sub(count);
    lines[start..].join("\n")
}

async fn list_encoders(ffmpeg: &Path, only_type: Option<char>) -> Vec<String> {
    let output = Command::new(ffmpeg)
        .args(["-hide_banner", "-encoders"])
        .output()
        .await;
    let Ok(output) = output else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let mut names = Vec::new();
    for line in text.lines() {
        let mut parts = line.split_whitespace();
        let Some(flags) = parts.next() else {
            continue;
        };
        let Some(name) = parts.next() else {
            continue;
        };
        if name == "=" || name == "------" {
            continue;
        }
        if flags.is_empty() || !flags.chars().next().unwrap().is_ascii_uppercase() {
            continue;
        }
        if let Some(kind) = only_type {
            if !flags.starts_with(kind) {
                continue;
            }
        }
        names.push(name.to_string());
    }
    names
}

async fn version_line(binary: &Path) -> String {
    let output = Command::new(binary).arg("-version").output().await;
    match output {
        Ok(output) => String::from_utf8_lossy(&output.stdout)
            .lines()
            .next()
            .unwrap_or("?")
            .to_string(),
        Err(error) => format!("error: {error}"),
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;

    pub struct FakeFfmpeg {
        probe: ProbeData,
        size: u64,
    }

    impl FakeFfmpeg {
        pub fn new(probe: ProbeData) -> Self {
            Self { probe, size: 100 }
        }
    }

    #[async_trait]
    impl FfmpegBackend for FakeFfmpeg {
        fn ffmpeg_path(&self) -> PathBuf {
            PathBuf::from("ffmpeg")
        }

        fn ffprobe_path(&self) -> PathBuf {
            PathBuf::from("ffprobe")
        }

        async fn probe(&self, path: &Path) -> Result<MediaInfo> {
            Ok(MediaInfo::from_probe(path, self.probe.clone(), self.size))
        }

        async fn run(
            &self,
            _args: &[OsString],
            _duration: f64,
            _progress: Option<&ProgressSink>,
            _cancel: Option<&CancellationToken>,
        ) -> Result<()> {
            Ok(())
        }

        async fn has_encoder(
            &self,
            _encoder: &str,
            _input_args: &[String],
            _video_filter: Option<&str>,
        ) -> bool {
            false
        }

        async fn has_audio_encoder(&self, encoder: &str) -> bool {
            encoder == "aac" || encoder == "libfdk_aac"
        }

        async fn encoders(&self) -> Vec<String> {
            vec!["libx264".into(), "libx265".into()]
        }

        async fn audio_encoders(&self) -> Vec<String> {
            vec!["aac".into(), "libfdk_aac".into()]
        }

        async fn versions(&self) -> (String, String) {
            ("ffmpeg fake".into(), "ffprobe fake".into())
        }
    }

    #[test]
    fn parses_timecodes() {
        assert_eq!(parse_timecode("00:01:02.500"), Some(62.5));
        assert_eq!(
            parse_progress_line("out_time=00:00:10.000", 20.0),
            Some(0.5)
        );
        assert_eq!(parse_progress_line("out_time_us=5000000", 10.0), Some(0.5));
        assert_eq!(parse_progress_line("out_time_us=N/A", 10.0), None);
    }

    #[test]
    fn locates_absolute_binary() {
        let tool = locate_tool("/bin/sh");
        assert!(tool.is_some());
    }

    #[test]
    fn missing_absolute_binary_is_none() {
        assert!(locate_tool("/definitely/not/here/ffmpeg").is_none());
    }
}
