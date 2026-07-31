mod diagnostics;
mod sidecar;

use diagnostics::export_report;
use serde_json::Value;
use sidecar::{BridgeError, RpcBridge, SidecarStatus};
use std::sync::Arc;
use tauri::{Manager, State};

#[tauri::command]
async fn rpc_request(
    bridge: State<'_, Arc<RpcBridge>>,
    method: String,
    params: Value,
) -> Result<Value, BridgeError> {
    let bridge = Arc::clone(bridge.inner());
    tauri::async_runtime::spawn_blocking(move || bridge.request(&method, params))
        .await
        .map_err(|error| {
            BridgeError::new(
                "BRIDGE_TASK_FAILED",
                format!("RPC bridge task failed: {error}"),
                true,
            )
        })?
}

#[tauri::command]
fn sidecar_status(bridge: State<'_, Arc<RpcBridge>>) -> SidecarStatus {
    bridge.status()
}

#[tauri::command]
async fn sidecar_restart(bridge: State<'_, Arc<RpcBridge>>) -> Result<SidecarStatus, BridgeError> {
    let bridge = Arc::clone(bridge.inner());
    tauri::async_runtime::spawn_blocking(move || bridge.restart())
        .await
        .map_err(|error| {
            BridgeError::new(
                "BRIDGE_TASK_FAILED",
                format!("sidecar restart task failed: {error}"),
                true,
            )
        })?
}

#[tauri::command]
async fn export_diagnostics(
    app: tauri::AppHandle,
    bridge: State<'_, Arc<RpcBridge>>,
    diagnostics: Value,
) -> Result<Option<String>, BridgeError> {
    export_report(app, bridge.status(), diagnostics).await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            rpc_request,
            sidecar_status,
            sidecar_restart,
            export_diagnostics
        ])
        .setup(|app| {
            let bridge = Arc::new(RpcBridge::new(app.handle().clone()));
            app.manage(Arc::clone(&bridge));
            tauri::async_runtime::spawn_blocking(move || {
                if let Err(error) = bridge.start() {
                    eprintln!("failed to start DeepCode App Server: {error}");
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build DeepCode desktop");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(bridge) = app_handle.try_state::<Arc<RpcBridge>>() {
                bridge.shutdown();
            }
        }
    });
}
