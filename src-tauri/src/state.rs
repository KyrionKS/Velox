use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use tokio_util::sync::CancellationToken;
use velox_core::config::{self, Config};

pub struct AppState {
    pub config: Mutex<Config>,
    pub config_path: PathBuf,
    pub run: Mutex<Option<CancellationToken>>,
    config_error: Mutex<Option<String>>,
    shutting_down: AtomicBool,
}

impl AppState {
    pub fn new() -> Self {
        let config_path = config::user_config_path();
        let (config, config_error) = load_from(&config_path);
        Self {
            config: Mutex::new(config),
            config_path,
            run: Mutex::new(None),
            config_error: Mutex::new(config_error),
            shutting_down: AtomicBool::new(false),
        }
    }

    pub fn config(&self) -> Config {
        self.config.lock().unwrap().clone()
    }

    pub fn config_error(&self) -> Option<String> {
        self.config_error.lock().unwrap().clone()
    }

    pub fn clear_config_error(&self) {
        *self.config_error.lock().unwrap() = None;
    }

    /// Re-read the config from disk, so the Reload button actually sees
    /// external edits. Keeps the current config when the file is invalid.
    pub fn reload_config(&self) -> std::result::Result<Config, String> {
        let (config, error) = load_from(&self.config_path);
        if let Some(error) = error {
            *self.config_error.lock().unwrap() = Some(error.clone());
            return Err(error);
        }
        *self.config.lock().unwrap() = config.clone();
        *self.config_error.lock().unwrap() = None;
        Ok(config)
    }

    pub fn is_running(&self) -> bool {
        self.run.lock().unwrap().is_some()
    }

    /// Request a clean shutdown. Returns true when this is the first request.
    pub fn request_shutdown(&self) -> bool {
        let first = !self.shutting_down.swap(true, Ordering::SeqCst);
        if let Some(token) = self.run.lock().unwrap().as_ref() {
            token.cancel();
        }
        first
    }

    pub fn is_shutting_down(&self) -> bool {
        self.shutting_down.load(Ordering::SeqCst)
    }
}

fn load_from(path: &std::path::Path) -> (Config, Option<String>) {
    if !path.exists() {
        return (Config::default(), None);
    }
    match Config::load(path) {
        Ok(config) => (config, None),
        Err(error) => (Config::default(), Some(error.to_string())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn broken_config_is_reported_and_defaults_are_used() {
        let path =
            std::env::temp_dir().join(format!("velox-broken-config-{}.toml", std::process::id()));
        std::fs::write(&path, "[general\nkeep_original = false\n").unwrap();
        let (config, error) = load_from(&path);
        let _ = std::fs::remove_file(&path);
        assert!(error.is_some());
        assert_eq!(config.general.ffmpeg_path, "ffmpeg");
    }

    #[test]
    fn reload_reads_external_changes_and_keeps_the_last_good_config() {
        let path =
            std::env::temp_dir().join(format!("velox-reload-config-{}.toml", std::process::id()));
        std::fs::write(
            &path,
            "[general]\nconcurrency = 5\nffmpeg_path = \"/opt/ffmpeg\"\n",
        )
        .unwrap();
        let state = AppState {
            config: Mutex::new(Config::default()),
            config_path: path.clone(),
            run: Mutex::new(None),
            config_error: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
        };
        let loaded = state.reload_config().expect("reload valid config");
        assert_eq!(loaded.general.concurrency, 5);
        assert_eq!(loaded.general.ffmpeg_path, "/opt/ffmpeg");
        assert!(state.config_error().is_none());

        std::fs::write(&path, "[general\nbroken = true\n").unwrap();
        assert!(state.reload_config().is_err());
        assert_eq!(state.config().general.concurrency, 5);
        assert!(state.config_error().is_some());
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn shutdown_cancels_the_active_run_once() {
        let state = AppState::new();
        let token = CancellationToken::new();
        *state.run.lock().unwrap() = Some(token.clone());
        assert!(state.is_running());
        assert!(state.request_shutdown());
        assert!(token.is_cancelled());
        assert!(!state.request_shutdown());
        assert!(state.is_shutting_down());
    }
}
