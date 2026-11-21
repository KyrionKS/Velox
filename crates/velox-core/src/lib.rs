//! Velox core: analyse, remux and transcode video files with ffmpeg.

pub mod config;
pub mod error;
pub mod events;
pub mod ffmpeg;
pub mod media;
pub mod pipeline;
pub mod plugins;
pub mod report;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
