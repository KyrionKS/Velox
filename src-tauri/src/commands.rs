use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::sync::Semaphore;
use tokio::task::JoinSet;
use tokio_util::sync::CancellationToken;

use velox_core::config::Config;
use velox_core::events::{EventSink, PipelineEvent};
use velox_core::ffmpeg::{Ffmpeg, FfmpegBackend};
use velox_core::media::MediaInfo;
use velox_core::pipeline::{Pipeline, ProcessOptions, RunReport};

use crate::state::AppState;

const VIDEO_EXTENSIONS: [&str; 15] = [
    "mkv", "mp4", "m4v", "avi", "mov", "ts", "m2ts", "mts", "webm", "wmv", "flv", "mpg", "mpeg",
    "vob", "mp2",
];

#[derive(Debug, Serialize)]
pub struct FileItem {
    pub path: String,
    pub name: String,
    pub size: u64,
}

#[derive(Debug, Serialize)]
pub struct FileInfo {
    pub path: String,
    pub name: String,
    pub size: u64,
    pub container: String,
    pub duration: f64,
    pub is_video: bool,
    pub video_codec: String,
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    pub video_kbps: Option<u32>,
    pub audio_count: u32,
    pub subtitle_count: u32,
    pub audio_kbps: u32,
    pub audio_languages: Vec<String>,
    pub subtitle_languages: Vec<String>,
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct EncoderInfo {
    pub ffmpeg: String,
    pub ffprobe: String,
    pub video: Vec<String>,
    pub audio: Vec<String>,
}

#[tauri::command]
pub fn get_config(state: State<'_, AppState>) -> Config {
    state.config()
}

#[tauri::command]
pub fn config_path(state: State<'_, AppState>) -> String {
    state.config_path.to_string_lossy().into_owned()
}

#[tauri::command]
pub fn save_config(config: Config, state: State<'_, AppState>) -> Result<(), String> {
    {
        let mut current = state.config.lock().unwrap();
        *current = config.clone();
    }
    config
        .save(&state.config_path)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn detect_encoders(state: State<'_, AppState>) -> Result<EncoderInfo, String> {
    let config = state.config();
    let ffmpeg = Ffmpeg::resolve(&config.general.ffmpeg_path, &config.general.ffprobe_path)
        .map_err(|error| error.to_string())?;
    let ffmpeg_path = ffmpeg.ffmpeg_path().to_string_lossy().into_owned();
    let ffprobe_path = ffmpeg.ffprobe_path().to_string_lossy().into_owned();

    let video: Vec<String> = ffmpeg
        .encoders()
        .await
        .into_iter()
        .filter(|name| {
            name.contains("hevc")
                || name.contains("h264")
                || name.contains("av1")
                || name.contains("vp9")
        })
        .collect();
    let audio: Vec<String> = ffmpeg
        .audio_encoders()
        .await
        .into_iter()
        .filter(|name| {
            ["aac", "ac3", "eac3", "mp3", "flac", "opus", "vorbis", "fdk"]
                .iter()
                .any(|needle| name.contains(needle))
        })
        .collect();

    Ok(EncoderInfo {
        ffmpeg: ffmpeg_path,
        ffprobe: ffprobe_path,
        video,
        audio,
    })
}

#[tauri::command]
pub async fn probe_file(path: String, state: State<'_, AppState>) -> Result<MediaInfo, String> {
    let config = state.config();
    let ffmpeg = Ffmpeg::resolve(&config.general.ffmpeg_path, &config.general.ffprobe_path)
        .map_err(|error| error.to_string())?;
    ffmpeg
        .probe(Path::new(&path))
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn probe_files(
    paths: Vec<String>,
    state: State<'_, AppState>,
) -> Result<Vec<FileInfo>, String> {
    let config = state.config();
    let ffmpeg = Arc::new(
        Ffmpeg::resolve(&config.general.ffmpeg_path, &config.general.ffprobe_path)
            .map_err(|error| error.to_string())?,
    );
    let semaphore = Arc::new(Semaphore::new(8));
    let mut set = JoinSet::new();
    for (index, path) in paths.into_iter().enumerate() {
        let ffmpeg = ffmpeg.clone();
        let semaphore = semaphore.clone();
        set.spawn(async move {
            let _permit = semaphore.acquire_owned().await.ok();
            (index, file_info(&ffmpeg, &path).await)
        });
    }
    let mut results: Vec<Option<FileInfo>> = (0..set.len()).map(|_| None).collect();
    while let Some(joined) = set.join_next().await {
        if let Ok((index, info)) = joined {
            if index < results.len() {
                results[index] = Some(info);
            }
        }
    }
    Ok(results.into_iter().flatten().collect())
}

async fn file_info(ffmpeg: &Ffmpeg, path: &str) -> FileInfo {
    let path_buf = PathBuf::from(path);
    let size = std::fs::metadata(&path_buf)
        .map(|meta| meta.len())
        .unwrap_or(0);
    let name = path_buf
        .file_name()
        .map(|value| value.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.to_string());
    let base = FileInfo {
        path: path.to_string(),
        name,
        size,
        container: path_buf
            .extension()
            .map(|value| value.to_string_lossy().to_lowercase())
            .unwrap_or_default(),
        duration: 0.0,
        is_video: false,
        video_codec: String::new(),
        width: 0,
        height: 0,
        fps: 0.0,
        video_kbps: None,
        audio_count: 0,
        subtitle_count: 0,
        audio_kbps: 0,
        audio_languages: Vec::new(),
        subtitle_languages: Vec::new(),
        error: None,
    };

    let media = match ffmpeg.probe(&path_buf).await {
        Ok(media) => media,
        Err(error) => {
            return FileInfo {
                error: Some(error.to_string()),
                ..base
            }
        }
    };

    let video = media.video_stream();
    let video_codec = video.map(|stream| stream.codec()).unwrap_or_default();
    let width = video.and_then(|stream| stream.width).unwrap_or(0);
    let height = video.and_then(|stream| stream.height).unwrap_or(0);
    let fps = video
        .and_then(|stream| {
            stream
                .avg_frame_rate
                .as_deref()
                .or(stream.r_frame_rate.as_deref())
        })
        .and_then(|raw| {
            let mut parts = raw.split('/');
            let numerator = parts.next()?.parse::<f64>().ok()?;
            let denominator = parts
                .next()
                .and_then(|value| value.parse::<f64>().ok())
                .unwrap_or(1.0);
            if denominator > 0.0 {
                Some(numerator / denominator)
            } else {
                None
            }
        })
        .unwrap_or(0.0);
    let video_kbps = video.and_then(|stream| stream.bitrate_kbps());

    let audio = media.streams_of(velox_core::media::AUDIO);
    let subtitles = media.streams_of(velox_core::media::SUBTITLE);
    let mut audio_languages: Vec<String> = audio
        .iter()
        .map(|stream| {
            let language = stream.language();
            if language.is_empty() {
                "und".to_string()
            } else {
                language
            }
        })
        .collect();
    audio_languages.sort();
    audio_languages.dedup();
    let mut subtitle_languages: Vec<String> = subtitles
        .iter()
        .map(|stream| {
            let language = stream.language();
            if language.is_empty() {
                "und".to_string()
            } else {
                language
            }
        })
        .collect();
    subtitle_languages.sort();
    subtitle_languages.dedup();
    let audio_kbps = audio
        .iter()
        .map(|stream| stream.bitrate_kbps().unwrap_or(256))
        .sum();

    FileInfo {
        container: media.container(),
        duration: media.duration(),
        is_video: media.is_video(),
        video_codec,
        width,
        height,
        fps,
        video_kbps,
        audio_count: audio.len() as u32,
        subtitle_count: subtitles.len() as u32,
        audio_kbps,
        audio_languages,
        subtitle_languages,
        ..base
    }
}

#[tauri::command]
pub fn scan_folder(path: String, recursive: bool) -> Result<Vec<FileItem>, String> {
    let root = PathBuf::from(&path);
    if !root.is_dir() {
        return Err(format!("Not a folder: {path}"));
    }
    let mut files = Vec::new();
    collect_folder(&root, recursive, &mut files);
    files.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(files)
}

fn collect_folder(dir: &Path, recursive: bool, files: &mut Vec<FileItem>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut entries: Vec<_> = entries.flatten().collect();
    entries.sort_by_key(|entry| entry.path());
    for entry in entries {
        let path = entry.path();
        if path.is_dir() {
            if recursive {
                collect_folder(&path, recursive, files);
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
        if !VIDEO_EXTENSIONS.contains(&extension.as_str()) {
            continue;
        }
        let size = std::fs::metadata(&path).map(|meta| meta.len()).unwrap_or(0);
        files.push(FileItem {
            path: path.to_string_lossy().into_owned(),
            name: path
                .file_name()
                .map(|value| value.to_string_lossy().into_owned())
                .unwrap_or_default(),
            size,
        });
    }
}

#[tauri::command]
pub async fn start_run(
    app: AppHandle,
    state: State<'_, AppState>,
    files: Vec<String>,
    dry_run: bool,
) -> Result<(), String> {
    if files.is_empty() {
        return Err("No files selected.".to_string());
    }
    {
        let guard = state.run.lock().unwrap();
        if guard.is_some() {
            return Err("A run is already active.".to_string());
        }
    }

    let config = state.config();
    config
        .save(&state.config_path)
        .map_err(|error| format!("Could not save settings: {error}"))?;

    let ffmpeg = Ffmpeg::resolve(&config.general.ffmpeg_path, &config.general.ffprobe_path)
        .map_err(|error| error.to_string())?;
    let pipeline = Arc::new(Pipeline::new(config.clone(), Arc::new(ffmpeg)));

    let cancel = CancellationToken::new();
    *state.run.lock().unwrap() = Some(cancel.clone());

    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<PipelineEvent>();
    let tx = Arc::new(tx);
    let sink: Arc<dyn EventSink> = tx.clone();
    let app_events = app.clone();
    tokio::spawn(async move {
        while let Some(event) = rx.recv().await {
            let _ = app_events.emit("velox://event", &event);
        }
    });

    let workers = config.general.concurrency.clamp(1, files.len());
    let app_handle = app.clone();
    let run_sink = sink.clone();
    tokio::spawn(async move {
        let total = files.len();
        run_sink.emit(PipelineEvent::RunStarted { total });
        let semaphore = Arc::new(Semaphore::new(workers));
        let mut set = JoinSet::new();

        for (index, file) in files.into_iter().enumerate() {
            let pipeline = pipeline.clone();
            let sink = run_sink.clone();
            let cancel = cancel.clone();
            let semaphore = semaphore.clone();
            set.spawn(async move {
                let _permit = semaphore.acquire_owned().await.ok();
                let report = pipeline
                    .process(Path::new(&file), &ProcessOptions { dry_run, cancel }, sink)
                    .await;
                (index, report)
            });
        }

        let mut results: Vec<Option<RunReport>> = (0..total).map(|_| None).collect();
        while let Some(joined) = set.join_next().await {
            match joined {
                Ok((index, report)) => results[index] = Some(report),
                Err(error) => tracing::error!("worker failed: {error}"),
            }
        }
        let reports: Vec<RunReport> = results.into_iter().flatten().collect();
        run_sink.emit(PipelineEvent::RunFinished { reports });

        if let Some(state) = app_handle.try_state::<AppState>() {
            *state.run.lock().unwrap() = None;
        }
    });

    Ok(())
}

#[tauri::command]
pub fn stop_run(state: State<'_, AppState>) -> Result<(), String> {
    if let Some(token) = state.run.lock().unwrap().as_ref() {
        token.cancel();
    }
    Ok(())
}
