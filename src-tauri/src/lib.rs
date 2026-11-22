mod commands;
mod logging;
mod menu;
mod state;

use std::time::Duration;

use state::AppState;
use tauri::{Manager, RunEvent, WindowEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    logging::init();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(AppState::new())
        .invoke_handler(tauri::generate_handler![
            commands::get_config,
            commands::config_path,
            commands::config_error,
            commands::reload_config,
            commands::save_config,
            commands::detect_encoders,
            commands::probe_file,
            commands::probe_files,
            commands::scan_folder,
            commands::start_run,
            commands::stop_run,
            commands::start_log_stream,
        ])
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_zoom(1.0);
            }
            menu::install(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let state = window.state::<AppState>();
                if state.is_running() {
                    api.prevent_close();
                    if state.request_shutdown() {
                        shutdown_when_idle(window.app_handle().clone());
                    }
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Velox");

    app.run(|app_handle, event| {
        if let RunEvent::ExitRequested { api, .. } = event {
            let state = app_handle.state::<AppState>();
            if state.is_running() {
                api.prevent_exit();
                if state.request_shutdown() {
                    shutdown_when_idle(app_handle.clone());
                }
            }
        }
    });
}

/// Cancel the active run, wait for the pipeline to clean up its scratch files,
/// then exit.  A hard timeout keeps a stuck ffmpeg from blocking the app.
fn shutdown_when_idle(app: tauri::AppHandle) {
    tauri::async_runtime::spawn(async move {
        let mut waited = 0u32;
        loop {
            if !app.state::<AppState>().is_running() {
                break;
            }
            if waited >= 300 {
                tracing::warn!("Timed out waiting for the active run; exiting anyway.");
                break;
            }
            waited += 1;
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        app.exit(0);
    });
}
