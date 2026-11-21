use tauri::menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder};
use tauri::{App, Emitter};

pub fn install(app: &mut App) -> tauri::Result<()> {
    let handle = app.handle();

    let settings = MenuItemBuilder::with_id("settings", "Settings…")
        .accelerator("CmdOrCtrl+,")
        .build(handle)?;
    let add_files = MenuItemBuilder::with_id("add_files", "Add Files…")
        .accelerator("CmdOrCtrl+O")
        .build(handle)?;
    let add_folder = MenuItemBuilder::with_id("add_folder", "Add Folder…")
        .accelerator("CmdOrCtrl+Shift+O")
        .build(handle)?;
    let start = MenuItemBuilder::with_id("start", "Start")
        .accelerator("CmdOrCtrl+R")
        .build(handle)?;
    let stop = MenuItemBuilder::with_id("stop", "Stop").build(handle)?;

    let app_menu = SubmenuBuilder::new(handle, "Velox")
        .item(&PredefinedMenuItem::about(
            handle,
            Some("About Velox"),
            None,
        )?)
        .separator()
        .item(&settings)
        .separator()
        .item(&PredefinedMenuItem::hide(handle, Some("Hide Velox"))?)
        .item(&PredefinedMenuItem::hide_others(
            handle,
            Some("Hide Others"),
        )?)
        .item(&PredefinedMenuItem::show_all(handle, Some("Show All"))?)
        .separator()
        .item(&PredefinedMenuItem::quit(handle, Some("Quit Velox"))?)
        .build()?;

    let file_menu = SubmenuBuilder::new(handle, "File")
        .item(&add_files)
        .item(&add_folder)
        .separator()
        .item(&start)
        .item(&stop)
        .separator()
        .item(&PredefinedMenuItem::close_window(
            handle,
            Some("Close Window"),
        )?)
        .build()?;

    let edit_menu = SubmenuBuilder::new(handle, "Edit")
        .item(&PredefinedMenuItem::undo(handle, Some("Undo"))?)
        .item(&PredefinedMenuItem::redo(handle, Some("Redo"))?)
        .separator()
        .item(&PredefinedMenuItem::cut(handle, Some("Cut"))?)
        .item(&PredefinedMenuItem::copy(handle, Some("Copy"))?)
        .item(&PredefinedMenuItem::paste(handle, Some("Paste"))?)
        .item(&PredefinedMenuItem::select_all(handle, Some("Select All"))?)
        .build()?;

    let window_menu = SubmenuBuilder::new(handle, "Window")
        .item(&PredefinedMenuItem::minimize(handle, Some("Minimize"))?)
        .item(&PredefinedMenuItem::fullscreen(
            handle,
            Some("Toggle Full Screen"),
        )?)
        .build()?;

    let menu = MenuBuilder::new(handle)
        .items(&[&app_menu, &file_menu, &edit_menu, &window_menu])
        .build()?;
    app.set_menu(menu)?;

    app.on_menu_event(|app, event| {
        let id = event.id().as_ref().to_string();
        let _ = app.emit("velox://menu", id);
    });

    Ok(())
}
