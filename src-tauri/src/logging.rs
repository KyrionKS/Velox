//! Captures backend warnings/errors and forwards them to the Activity log.
//!
//! The Tauri frontend installs its event listener and then calls
//! `start_log_stream`; messages produced earlier (for example config warnings)
//! are buffered in the channel until then.

use std::io::{self, Write};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Mutex, OnceLock};

use tauri::{AppHandle, Emitter};
use tracing_subscriber::fmt::MakeWriter;

static SENDER: OnceLock<Sender<String>> = OnceLock::new();
static RECEIVER: Mutex<Option<Receiver<String>>> = Mutex::new(None);

pub fn init() {
    let (tx, rx) = mpsc::channel();
    let _ = SENDER.set(tx);
    *RECEIVER.lock().unwrap() = Some(rx);

    let _ = tracing_subscriber::fmt()
        .with_max_level(tracing::Level::WARN)
        .with_target(false)
        .without_time()
        .with_ansi(false)
        .with_writer(ChannelWriter)
        .try_init();
}

pub fn start_stream(app: AppHandle) {
    let Some(receiver) = RECEIVER.lock().unwrap().take() else {
        return;
    };
    std::thread::spawn(move || {
        for line in receiver {
            let _ = app.emit("velox://log", line);
        }
    });
}

struct ChannelWriter;

impl Write for ChannelWriter {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        if let Some(sender) = SENDER.get() {
            let text = String::from_utf8_lossy(buffer);
            for line in text.lines().filter(|line| !line.trim().is_empty()) {
                let _ = sender.send(line.trim_end().to_string());
            }
        }
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> MakeWriter<'a> for ChannelWriter {
    type Writer = ChannelWriter;

    fn make_writer(&'a self) -> Self::Writer {
        ChannelWriter
    }
}
