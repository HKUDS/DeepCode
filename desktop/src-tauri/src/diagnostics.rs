use crate::sidecar::{BridgeError, SidecarStatus};
use serde_json::{json, Value};
use std::fs::OpenOptions;
use std::io::Write;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

const MAX_REPORT_BYTES: usize = 1024 * 1024;

pub(crate) async fn export_report(
    app: AppHandle,
    status: SidecarStatus,
    diagnostics: Value,
) -> Result<Option<String>, BridgeError> {
    tauri::async_runtime::spawn_blocking(move || export_report_blocking(app, status, diagnostics))
        .await
        .map_err(|error| {
            BridgeError::new(
                "DIAGNOSTICS_EXPORT_FAILED",
                format!("diagnostics export task failed: {error}"),
                true,
            )
        })?
}

fn export_report_blocking(
    app: AppHandle,
    status: SidecarStatus,
    diagnostics: Value,
) -> Result<Option<String>, BridgeError> {
    let generated_at_unix_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| {
            BridgeError::new(
                "DIAGNOSTICS_EXPORT_FAILED",
                format!("system clock is before the Unix epoch: {error}"),
                false,
            )
        })?
        .as_millis();
    let contents = report_bytes(status, diagnostics, generated_at_unix_ms)?;
    let filename = format!("deepcode-diagnostics-{}.json", generated_at_unix_ms / 1000);
    let Some(selected) = app
        .dialog()
        .file()
        .set_title("Export DeepCode diagnostics")
        .set_file_name(filename)
        .add_filter("JSON report", &["json"])
        .blocking_save_file()
    else {
        return Ok(None);
    };
    let path = selected.into_path().map_err(|error| {
        BridgeError::new(
            "DIAGNOSTICS_EXPORT_FAILED",
            format!("the selected diagnostics path is invalid: {error}"),
            false,
        )
    })?;
    let mut options = OpenOptions::new();
    options.create(true).truncate(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&path).map_err(|error| {
        BridgeError::new(
            "DIAGNOSTICS_EXPORT_FAILED",
            format!("failed to create diagnostics report: {error}"),
            true,
        )
    })?;
    file.write_all(&contents).map_err(|error| {
        BridgeError::new(
            "DIAGNOSTICS_EXPORT_FAILED",
            format!("failed to write diagnostics report: {error}"),
            true,
        )
    })?;
    file.sync_all().map_err(|error| {
        BridgeError::new(
            "DIAGNOSTICS_EXPORT_FAILED",
            format!("failed to flush diagnostics report: {error}"),
            true,
        )
    })?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

fn report_bytes(
    status: SidecarStatus,
    diagnostics: Value,
    generated_at_unix_ms: u128,
) -> Result<Vec<u8>, BridgeError> {
    let report = json!({
        "formatVersion": 1,
        "generatedAtUnixMs": generated_at_unix_ms,
        "diagnostics": diagnostics,
        "sidecar": status,
        "privacy": {
            "containsCredentials": false,
            "containsPrompts": false,
            "containsFileContents": false
        }
    });
    let mut contents = serde_json::to_vec_pretty(&report).map_err(|error| {
        BridgeError::new(
            "DIAGNOSTICS_EXPORT_FAILED",
            format!("failed to serialize diagnostics report: {error}"),
            false,
        )
    })?;
    contents.push(b'\n');
    if contents.len() > MAX_REPORT_BYTES {
        return Err(BridgeError::new(
            "DIAGNOSTICS_REPORT_TOO_LARGE",
            "the sanitized diagnostics report exceeds the 1 MiB limit",
            false,
        ));
    }
    Ok(contents)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sidecar::SidecarPhase;

    fn status() -> SidecarStatus {
        SidecarStatus {
            phase: SidecarPhase::Ready,
            message: None,
            launch_source: Some("test".into()),
            server_info: Some(json!({"protocolVersion": "1.0"})),
        }
    }

    #[test]
    fn report_contains_only_explicit_sanitized_sections() {
        let bytes =
            report_bytes(status(), json!({"appVersion": "1.2.0", "checks": []}), 1234).unwrap();
        let report: Value = serde_json::from_slice(&bytes).unwrap();

        assert_eq!(report["formatVersion"], 1);
        assert_eq!(report["generatedAtUnixMs"], 1234);
        assert_eq!(report["diagnostics"]["appVersion"], "1.2.0");
        assert_eq!(report["sidecar"]["phase"], "ready");
        assert_eq!(report["privacy"]["containsCredentials"], false);
    }

    #[test]
    fn report_rejects_unbounded_frontend_payloads() {
        let error = report_bytes(
            status(),
            json!({"unexpected": "x".repeat(MAX_REPORT_BYTES)}),
            1234,
        )
        .unwrap_err();

        assert_eq!(error.code, "DIAGNOSTICS_REPORT_TOO_LARGE");
    }
}
