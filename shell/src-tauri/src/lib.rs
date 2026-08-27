//! Warraq desktop shell.
//!
//! The shell owns no conversion logic. It spawns the Python engine sidecar and
//! relays JSON-RPC between the WebView UI and that process, so the GUI and the
//! CLI can never diverge in behaviour.

mod engine;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            app.manage(engine::EngineState::default());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            engine::engine_start,
            engine::engine_stop,
            engine::engine_capabilities,
            engine::engine_analyze,
            engine::engine_convert,
            engine::engine_cancel,
            engine::engine_status,
            engine::engine_get_settings,
            engine::engine_set_settings,
            engine::engine_test_azure,
            engine::existing_dirs,
            engine::discover_output_dirs,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Warraq");
}
