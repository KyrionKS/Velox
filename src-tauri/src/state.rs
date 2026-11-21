use std::path::PathBuf;
use std::sync::Mutex;

use tokio_util::sync::CancellationToken;
use velox_core::config::{self, Config};

pub struct AppState {
    pub config: Mutex<Config>,
    pub config_path: PathBuf,
    pub run: Mutex<Option<CancellationToken>>,
}

impl AppState {
    pub fn new() -> Self {
        let config_path = config::user_config_path();
        let config = config::load_user_config();
        Self {
            config: Mutex::new(config),
            config_path,
            run: Mutex::new(None),
        }
    }

    pub fn config(&self) -> Config {
        self.config.lock().unwrap().clone()
    }
}
