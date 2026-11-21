mod commands;
mod menu;
mod state;

use state::AppState;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(AppState::new())
        .invoke_handler(tauri::generate_handler![
            commands::get_config,
            commands::config_path,
            commands::save_config,
            commands::detect_encoders,
            commands::probe_file,
            commands::probe_files,
            commands::scan_folder,
            commands::start_run,
            commands::stop_run,
        ])
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_zoom(1.0);
            }
            menu::install(app)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Velox");
}
