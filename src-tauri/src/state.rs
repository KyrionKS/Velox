use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use tokio_util::sync::CancellationToken;
use velox_core::config::{self, Config};

pub struct AppState {
    pub config: Mutex<Config>,
    pub config_path: PathBuf,
    pub run: Mutex<Option<CancellationToken>>,
    shutting_down: AtomicBool,
}

impl AppState {
    pub fn new() -> Self {
        let config_path = config::user_config_path();
        let config = config::load_user_config();
        Self {
            config: Mutex::new(config),
            config_path,
            run: Mutex::new(None),
            shutting_down: AtomicBool::new(false),
        }
    }

    pub fn config(&self) -> Config {
        self.config.lock().unwrap().clone()
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

#[cfg(test)]
mod tests {
    use super::*;

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
