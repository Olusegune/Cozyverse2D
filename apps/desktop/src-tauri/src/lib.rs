//! Tauri host. Starts the local Python worker on a random loopback port with a per-session
//! bearer token (generated here, passed by environment, never written to disk or logs) and
//! hands the connection to the UI. No network access beyond 127.0.0.1.

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use rand::distributions::Alphanumeric;
use rand::Rng;
use serde::Serialize;
use tauri::{Manager, RunEvent, State};

#[derive(Clone, Serialize)]
struct Conn {
    base: String,
    token: String,
}

#[derive(Default)]
struct Worker {
    conn: Option<Conn>,
    child: Option<Child>,
}

struct WorkerState(Mutex<Worker>);

#[cfg(windows)]
fn no_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
}
#[cfg(not(windows))]
fn no_window(_cmd: &mut Command) {}

fn data_dir() -> PathBuf {
    if let Ok(p) = std::env::var("CV_DATA_DIR") {
        return PathBuf::from(p);
    }
    let base = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into());
    PathBuf::from(base).join("CozyVerse2D")
}

/// (python executable, directory containing the `cozyverse_worker` package)
fn locate_worker(app: &tauri::AppHandle) -> Result<(PathBuf, PathBuf), String> {
    if let Ok(py) = std::env::var("CV_PYTHON") {
        let root = std::env::var("CV_WORKER_DIR").map_err(|_| "CV_PYTHON set without CV_WORKER_DIR")?;
        return Ok((PathBuf::from(py), PathBuf::from(root)));
    }
    #[cfg(debug_assertions)]
    {
        let _ = app;
        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let py = repo.join(".venv/Scripts/python.exe");
        if py.is_file() {
            return Ok((py, repo.join("worker")));
        }
        return Err("Dev worker not found. Create the repo .venv first (see README).".into());
    }
    #[cfg(not(debug_assertions))]
    {
        let res = app.path().resource_dir().map_err(|e| e.to_string())?;
        let py = res.join("worker/python/python.exe");
        if py.is_file() {
            return Ok((py, res.join("worker")));
        }
        Err("The bundled Python worker is missing from this build.".into())
    }
}

#[tauri::command]
fn worker_connection(app: tauri::AppHandle, state: State<WorkerState>) -> Result<Conn, String> {
    let mut w = state.0.lock().map_err(|_| "worker state poisoned")?;
    if let Some(c) = &w.conn {
        return Ok(c.clone());
    }
    let (python, worker_dir) = locate_worker(&app)?;
    let token: String = rand::thread_rng().sample_iter(&Alphanumeric).take(48).map(char::from).collect();
    let mut cmd = Command::new(python);
    cmd.args(["-s", "-m", "cozyverse_worker", "--port", "0"])
        .current_dir(&worker_dir)
        .env("PYTHONPATH", &worker_dir)
        .env("CV_WORKER_TOKEN", &token)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .stdin(Stdio::null());
    no_window(&mut cmd);
    let mut child = cmd.spawn().map_err(|e| format!("could not start worker: {e}"))?;
    let stdout = child.stdout.take().ok_or("no worker stdout")?;
    let mut line = String::new();
    BufReader::new(stdout).read_line(&mut line).map_err(|e| e.to_string())?;
    let v: serde_json::Value = serde_json::from_str(line.trim()).map_err(|_| "worker did not report a port")?;
    let port = v.get("port").and_then(|p| p.as_u64()).ok_or("worker reported no port")?;
    let conn = Conn { base: format!("http://127.0.0.1:{port}"), token };
    w.conn = Some(conn.clone());
    w.child = Some(child);
    Ok(conn)
}

/// Open a folder in Explorer, but only inside the CozyVerse data directory.
#[tauri::command]
fn open_folder(path: String) -> Result<(), String> {
    let target = std::fs::canonicalize(&path).map_err(|_| "folder does not exist")?;
    let root = std::fs::canonicalize(data_dir()).map_err(|_| "data directory missing")?;
    if !target.starts_with(&root) || !target.is_dir() {
        return Err("that folder is outside the CozyVerse data directory".into());
    }
    let mut cmd = Command::new("explorer.exe");
    cmd.arg(target);
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}

fn stop_worker(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<WorkerState>() {
        if let Ok(mut w) = state.0.lock() {
            if let Some(mut child) = w.child.take() {
                let pid = child.id().to_string();
                let mut k = Command::new("taskkill");
                k.args(["/PID", &pid, "/T", "/F"]).stdout(Stdio::null()).stderr(Stdio::null());
                no_window(&mut k);
                let _ = k.status(); // kills the worker and any Blender it spawned
                let _ = child.kill();
            }
        }
    }
}

pub fn run() {
    tauri::Builder::default()
        .manage(WorkerState(Mutex::new(Worker::default())))
        .invoke_handler(tauri::generate_handler![worker_connection, open_folder])
        .build(tauri::generate_context!())
        .expect("error while building CozyVerse Breakdown")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_worker(app);
            }
        });
}
