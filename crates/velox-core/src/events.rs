//! Progress/event layer shared by the CLI, the Tauri app and tests.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::pipeline::RunReport;
use crate::plugins::RemuxPlan;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PassEvent {
    pub steps: Vec<String>,
    /// `remux`, `transcode` or the plugin key that produced the pass.
    pub kind: String,
    pub input_bytes: u64,
    pub output_bytes: u64,
    #[serde(default)]
    pub command: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub plan: Option<RemuxPlan>,
}

impl PassEvent {
    pub fn saved_bytes(&self) -> i64 {
        self.input_bytes as i64 - self.output_bytes as i64
    }

    pub fn label(&self) -> String {
        self.steps.join(" + ")
    }

    pub fn describe(&self) -> String {
        let saved = self.saved_bytes();
        let sign = if saved >= 0 { "\u{2212}" } else { "+" };
        format!(
            "{}: {} \u{2192} {} ({}{})",
            self.label(),
            fmt_bytes(self.input_bytes),
            fmt_bytes(self.output_bytes),
            sign,
            fmt_bytes(saved.unsigned_abs())
        )
    }
}

/// One progress event.  The Tauri layer serialises these directly; the CLI
/// ignores the chatty ones and prints the readable messages.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum PipelineEvent {
    RunStarted {
        total: usize,
    },
    FileStarted {
        path: PathBuf,
        summary: String,
    },
    StepStarted {
        path: PathBuf,
        key: String,
        name: String,
    },
    StepNote {
        path: PathBuf,
        message: String,
    },
    PassStarted {
        path: PathBuf,
        description: String,
    },
    Progress {
        path: PathBuf,
        fraction: f64,
    },
    PassFinished {
        path: PathBuf,
        pass: Box<PassEvent>,
    },
    FileFinished {
        report: Box<RunReport>,
    },
    RunFinished {
        reports: Vec<RunReport>,
    },
    /// Plain log line for the GUI's details panel.
    Log {
        message: String,
    },
    Error {
        path: Option<PathBuf>,
        message: String,
    },
}

pub trait EventSink: Send + Sync {
    fn emit(&self, event: PipelineEvent);
}

impl EventSink for tokio::sync::mpsc::UnboundedSender<PipelineEvent> {
    fn emit(&self, event: PipelineEvent) {
        let _ = self.send(event);
    }
}

impl EventSink for std::sync::mpsc::Sender<PipelineEvent> {
    fn emit(&self, event: PipelineEvent) {
        let _ = self.send(event);
    }
}

#[derive(Debug, Default, Clone, Copy)]
pub struct NoopSink;

impl EventSink for NoopSink {
    fn emit(&self, _event: PipelineEvent) {}
}

/// Human readable size using decimal units, like Finder.
pub fn fmt_bytes(num: u64) -> String {
    let mut value = num as f64;
    for unit in ["B", "KB", "MB", "GB", "TB"] {
        if value.abs() < 1000.0 || unit == "TB" {
            if unit == "B" {
                return format!("{} {unit}", value as u64);
            }
            return format!("{value:.2} {unit}");
        }
        value /= 1000.0;
    }
    format!("{value:.2} TB")
}

pub fn fmt_duration(seconds: f64) -> String {
    let total = seconds.max(0.0) as u64;
    let hours = total / 3600;
    let minutes = (total % 3600) / 60;
    let secs = total % 60;
    if hours > 0 {
        format!("{hours}:{minutes:02}:{secs:02}")
    } else {
        format!("{minutes}:{secs:02}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_sizes() {
        assert_eq!(fmt_bytes(0), "0 B");
        assert_eq!(fmt_bytes(999), "999 B");
        assert_eq!(fmt_bytes(1000), "1.00 KB");
        assert_eq!(fmt_bytes(1_500_000), "1.50 MB");
    }

    #[test]
    fn pass_event_describes_savings() {
        let event = PassEvent {
            steps: vec!["Clean Audio".into(), "Order Streams".into()],
            kind: "remux".into(),
            input_bytes: 2_000_000,
            output_bytes: 1_500_000,
            command: vec![],
            plan: None,
        };
        assert_eq!(event.label(), "Clean Audio + Order Streams");
        assert_eq!(
            event.describe(),
            "Clean Audio + Order Streams: 2.00 MB \u{2192} 1.50 MB (\u{2212}500.00 KB)"
        );
        assert_eq!(event.saved_bytes(), 500_000);
    }
}
