//! Engine sidecar bridge.
//!
//! Spawns the Python conversion engine, writes JSON-RPC requests to its stdin
//! and reads newline-delimited JSON from its stdout. Terminal messages
//! (`result` / `error`) resolve the matching request; everything else is
//! streamed to the UI as a Tauri event so progress can be rendered live.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, State};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// How long to wait for a terminal reply. Conversions of very large books can
/// legitimately run for many minutes.
const REPLY_TIMEOUT_SECS: u64 = 60 * 60;

#[derive(Default)]
pub struct EngineState {
    inner: Arc<Mutex<Option<EngineProcess>>>,
}

struct EngineProcess {
    child: Child,
    stdin: ChildStdin,
    /// Requests awaiting a terminal `result` / `error` message.
    pending: Arc<Mutex<HashMap<String, Sender<Value>>>>,
}

impl EngineState {
    fn with<T>(&self, f: impl FnOnce(&mut EngineProcess) -> Result<T, String>)
        -> Result<T, String>
    {
        let mut guard = self.inner.lock().map_err(|e| e.to_string())?;
        let proc = guard.as_mut().ok_or("engine is not running")?;
        f(proc)
    }
}

/// Locate the engine executable.
///
/// In a packaged build it sits next to the shell binary; during development we
/// fall back to the PyInstaller output, then to running from source.
fn engine_command() -> Result<Command, String> {
    let exe_dir = std::env::current_exe()
        .map_err(|e| e.to_string())?
        .parent()
        .ok_or("no parent dir")?
        .to_path_buf();

    let bundled = exe_dir.join("engine").join("warraq-engine.exe");
    if bundled.exists() {
        return Ok(Command::new(bundled));
    }

    // repo-relative dev locations
    let repo = exe_dir
        .ancestors()
        .find(|p| p.join("engine").join("kbo").exists())
        .map(|p| p.to_path_buf());

    if let Some(root) = repo {
        let frozen = root
            .join("engine")
            .join("dist")
            .join("warraq-engine")
            .join("warraq-engine.exe");
        if frozen.exists() {
            return Ok(Command::new(frozen));
        }
        let mut c = Command::new("python");
        c.arg("-u").arg("-m").arg("kbo.cli");
        c.current_dir(root.join("engine"));
        return Ok(c);
    }

    Err("could not locate the Warraq engine".into())
}

#[tauri::command]
pub fn engine_start(app: AppHandle, state: State<EngineState>) -> Result<Value, String> {
    let mut guard = state.inner.lock().map_err(|e| e.to_string())?;
    if guard.is_some() {
        return Ok(json!({ "alreadyRunning": true }));
    }

    let mut cmd = engine_command()?;
    cmd.arg("--rpc")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let mut child = cmd.spawn().map_err(|e| format!("spawn failed: {e}"))?;
    let stdin = child.stdin.take().ok_or("no stdin")?;
    let stdout = child.stdout.take().ok_or("no stdout")?;
    let pending: Arc<Mutex<HashMap<String, Sender<Value>>>> =
        Arc::new(Mutex::new(HashMap::new()));

    // Reader thread: terminal messages resolve a pending request, and every
    // message is forwarded to the UI so progress can be rendered live.
    let pending_reader = Arc::clone(&pending);
    let app_handle = app.clone();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            let Ok(line) = line else { break };
            if line.trim().is_empty() {
                continue;
            }
            let Ok(msg) = serde_json::from_str::<Value>(&line) else {
                continue;
            };

            let is_terminal = msg.get("result").is_some() || msg.get("error").is_some();
            if is_terminal {
                if let Some(id) = msg.get("id").and_then(|v| v.as_str()) {
                    let tx = pending_reader
                        .lock()
                        .ok()
                        .and_then(|mut m| m.remove(id));
                    if let Some(tx) = tx {
                        let _ = tx.send(msg.clone());
                    }
                }
            }
            let _ = app_handle.emit("engine://message", msg);
        }
        let _ = app_handle.emit("engine://closed", json!({}));
    });

    *guard = Some(EngineProcess {
        child,
        stdin,
        pending,
    });
    // The UI waits for the `ready` banner on engine://message rather than
    // blocking here, so the window can paint immediately.
    Ok(json!({ "started": true }))
}

fn request(proc: &mut EngineProcess, method: &str, params: Value)
    -> Result<Value, String>
{
    let id = uuid::Uuid::new_v4().to_string();
    let (tx, rx) = channel::<Value>();
    proc.pending
        .lock()
        .map_err(|e| e.to_string())?
        .insert(id.clone(), tx);

    let payload = json!({ "id": id, "method": method, "params": params });
    writeln!(proc.stdin, "{payload}").map_err(|e| e.to_string())?;
    proc.stdin.flush().map_err(|e| e.to_string())?;

    let msg = rx
        .recv_timeout(std::time::Duration::from_secs(REPLY_TIMEOUT_SECS))
        .map_err(|_| "engine did not reply in time".to_string())?;

    if let Some(err) = msg.get("error") {
        return Err(err
            .get("message")
            .and_then(|m| m.as_str())
            .unwrap_or("engine error")
            .to_string());
    }
    Ok(msg.get("result").cloned().unwrap_or(json!({})))
}

/// Fire-and-forget: the caller tracks progress through `engine://message`.
fn notify(proc: &mut EngineProcess, id: &str, method: &str, params: Value)
    -> Result<(), String>
{
    let payload = json!({ "id": id, "method": method, "params": params });
    writeln!(proc.stdin, "{payload}").map_err(|e| e.to_string())?;
    proc.stdin.flush().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn engine_capabilities(state: State<EngineState>) -> Result<Value, String> {
    state.with(|p| request(p, "capabilities", json!({})))
}

#[tauri::command]
pub fn engine_analyze(state: State<EngineState>, path: String, sample_pages: Option<u32>)
    -> Result<Value, String>
{
    state.with(|p| {
        request(
            p,
            "analyze",
            json!({ "path": path, "samplePages": sample_pages.unwrap_or(14) }),
        )
    })
}

/// Starts a conversion and returns its job id immediately. Progress arrives as
/// `engine://message` events carrying the same id.
#[tauri::command]
pub fn engine_convert(state: State<EngineState>, options: Value)
    -> Result<Value, String>
{
    let job_id = uuid::Uuid::new_v4().to_string();
    state.with(|p| {
        notify(p, &job_id, "convert", options.clone())?;
        Ok(json!({ "jobId": job_id }))
    })
}

#[tauri::command]
pub fn engine_cancel(state: State<EngineState>, job_id: String) -> Result<Value, String> {
    state.with(|p| request(p, "cancel", json!({ "jobId": job_id })))
}

#[tauri::command]
pub fn engine_status(state: State<EngineState>) -> Result<Value, String> {
    let guard = state.inner.lock().map_err(|e| e.to_string())?;
    Ok(json!({ "running": guard.is_some() }))
}

#[tauri::command]
pub fn engine_stop(state: State<EngineState>) -> Result<Value, String> {
    let mut guard = state.inner.lock().map_err(|e| e.to_string())?;
    if let Some(mut p) = guard.take() {
        let _ = writeln!(p.stdin, "{}", json!({"id":"stop","method":"shutdown"}));
        let _ = p.stdin.flush();
        std::thread::sleep(std::time::Duration::from_millis(400));
        let _ = p.child.kill();
        let _ = p.child.wait();
    }
    Ok(json!({ "stopped": true }))
}
