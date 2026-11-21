//! Error types used across Velox.

use std::path::PathBuf;

use thiserror::Error;

pub type Result<T, E = VeloxError> = std::result::Result<T, E>;

#[derive(Debug, Error)]
pub enum VeloxError {
    #[error("{message}")]
    ToolNotFound { message: String },

    #[error("{message}")]
    Ffmpeg {
        message: String,
        command: Vec<String>,
    },

    #[error("{0}")]
    Plugin(String),

    #[error("Processing stopped by user")]
    Cancelled,

    #[error("{0}")]
    Config(String),

    #[error("{0}")]
    Io(#[from] std::io::Error),

    #[error("{0}")]
    Other(String),
}

impl VeloxError {
    pub fn tool_not_found(message: impl Into<String>) -> Self {
        Self::ToolNotFound {
            message: message.into(),
        }
    }

    pub fn ffmpeg(message: impl Into<String>, command: Vec<String>) -> Self {
        Self::Ffmpeg {
            message: message.into(),
            command,
        }
    }

    pub fn config(message: impl Into<String>) -> Self {
        Self::Config(message.into())
    }

    pub fn plugin(message: impl Into<String>) -> Self {
        Self::Plugin(message.into())
    }

    pub fn other(message: impl Into<String>) -> Self {
        Self::Other(message.into())
    }
}

pub fn display_command(program: &std::path::Path, args: &[std::ffi::OsString]) -> Vec<String> {
    let mut command = Vec::with_capacity(args.len() + 1);
    command.push(program.to_string_lossy().into_owned());
    command.extend(args.iter().map(|arg| arg.to_string_lossy().into_owned()));
    command
}

pub fn path_display(path: &std::path::Path) -> String {
    path.to_string_lossy().into_owned()
}

pub fn missing_tool(tool: &str) -> VeloxError {
    let hint = if cfg!(target_os = "macos") {
        "brew install ffmpeg"
    } else {
        "install ffmpeg with your package manager"
    };
    VeloxError::tool_not_found(format!(
        "{tool} not found (looked for {tool:?}). Install it ({hint}) or set general.{tool}_path in your config."
    ))
}

pub fn path_buf(value: &str) -> PathBuf {
    PathBuf::from(value)
}
