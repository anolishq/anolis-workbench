use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

/// Workaround for WebKitGTK blank-screen issue on Linux systems where GPU
/// hardware compositing (DMA-BUF / GBM) fails — commonly observed with NVIDIA
/// GPUs running the nouveau driver or in environments without proper DRI/GBM
/// support.  When compositing fails the rendering pipeline freezes, which
/// blocks the WebView event loop and results in a blank window.
///
/// Setting these environment variables before GTK/WebKit initialization forces
/// software rendering which avoids the GPU path entirely.
///
/// Ref: <https://github.com/tauri-apps/tauri/issues/8254>
#[cfg(target_os = "linux")]
fn apply_linux_webview_workarounds() {
    use std::env;
    if env::var_os("WEBKIT_DISABLE_COMPOSITING_MODE").is_none() {
        env::set_var("WEBKIT_DISABLE_COMPOSITING_MODE", "1");
    }
    if env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
}

const SIDECAR_NAME: &str = "anolis-workbench-sidecar";
const WORKBENCH_HOST: &str = "127.0.0.1";
const WORKBENCH_PORT: u16 = 3010;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(20);
const HEALTH_POLL_INTERVAL: Duration = Duration::from_millis(250);

#[derive(Default)]
struct SidecarState {
    child: Mutex<Option<CommandChild>>,
}

fn start_sidecar(app: &AppHandle) -> bool {
    let state = app.state::<SidecarState>();
    let mut guard = match state.child.lock() {
        Ok(guard) => guard,
        Err(err) => {
            eprintln!("[desktop] failed to acquire sidecar lock: {err}");
            return false;
        }
    };

    if guard.is_some() {
        return true;
    }

    let command = match app.shell().sidecar(SIDECAR_NAME) {
        Ok(command) => command,
        Err(err) => {
            eprintln!("[desktop] failed to resolve sidecar '{SIDECAR_NAME}': {err}");
            return false;
        }
    };

    let (mut events, child) = match command
        .env("ANOLIS_WORKBENCH_OPEN_BROWSER", "0")
        .env("ANOLIS_WORKBENCH_HOST", WORKBENCH_HOST)
        .env("ANOLIS_WORKBENCH_PORT", WORKBENCH_PORT.to_string())
        .args(["--no-browser"])
        .spawn()
    {
        Ok(result) => result,
        Err(err) => {
            eprintln!("[desktop] failed to spawn sidecar: {err}");
            return false;
        }
    };

    tauri::async_runtime::spawn(async move {
        while events.recv().await.is_some() {
            // Keep the channel drained; we only care about process lifecycle,
            // not sidecar stdout/stderr in the desktop shell.
        }
    });

    *guard = Some(child);
    true
}

fn stop_sidecar(app: &AppHandle) {
    let state = app.state::<SidecarState>();
    let mut guard = match state.child.lock() {
        Ok(guard) => guard,
        Err(err) => {
            eprintln!("[desktop] failed to acquire sidecar lock during shutdown: {err}");
            return;
        }
    };

    let Some(child) = guard.take() else {
        return;
    };

    if let Err(err) = child.kill() {
        eprintln!("[desktop] failed to kill sidecar cleanly: {err}");
    }
}

fn check_health_once() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], WORKBENCH_PORT));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };

    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));

    let request =
        format!("GET /api/status HTTP/1.1\r\nHost: {WORKBENCH_HOST}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }

    let status_ok = response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200");
    if !status_ok {
        return false;
    }

    // Ensure we reached workbench and not an unrelated process returning 200 on :3010.
    let body = match response.split_once("\r\n\r\n") {
        Some((_, body)) => body,
        None => return false,
    };

    body.contains("\"workbench\"") && body.contains("\"composer\"")
}

fn wait_for_workbench_ready(timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if check_health_once() {
            return true;
        }
        thread::sleep(HEALTH_POLL_INTERVAL);
    }
    false
}

fn show_startup_error(app: &AppHandle) {
    let message = format!(
        "Unable to start Anolis Workbench on port {WORKBENCH_PORT}.\n\n\
The desktop wrapper reserves port {WORKBENCH_PORT}.\n\
Stop any process already bound to that port and relaunch the app."
    );

    app.dialog()
        .message(message)
        .title("Anolis Workbench startup failed")
        .buttons(MessageDialogButtons::Ok)
        .kind(MessageDialogKind::Error)
        .show(|_| {});
}

fn main() {
    #[cfg(target_os = "linux")]
    apply_linux_webview_workarounds();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            app.manage(SidecarState::default());

            if !start_sidecar(app.handle()) {
                show_startup_error(app.handle());
                std::process::exit(1);
            }

            if !wait_for_workbench_ready(STARTUP_TIMEOUT) {
                stop_sidecar(app.handle());
                show_startup_error(app.handle());
                std::process::exit(1);
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Anolis Workbench desktop shell")
        .run(|app_handle, event| {
            if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
                stop_sidecar(app_handle);
            }
        });
}
