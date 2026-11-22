use std::collections::VecDeque;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Manager, RunEvent, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
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
/// How many of the sidecar's last stderr lines a startup failure quotes.
const STDERR_TAIL_LINES: usize = 5;

#[derive(Default)]
struct SidecarState {
    child: Mutex<Option<CommandChild>>,
}

/// What the sidecar's process events have said so far: its last stderr lines,
/// and why it ended, once it has.
#[derive(Default)]
struct SidecarExit {
    stderr_tail: VecDeque<String>,
    ended: Option<String>,
}

impl SidecarExit {
    /// Why the sidecar ended, with its last stderr lines; None while it runs.
    fn reason(&self) -> Option<String> {
        let ended = self.ended.as_ref()?;
        if self.stderr_tail.is_empty() {
            return Some(ended.clone());
        }
        let tail: Vec<&str> = self.stderr_tail.iter().map(String::as_str).collect();
        Some(format!("{ended}. Its last output:\n{}", tail.join("\n")))
    }
}

/// An id for this launch. The sidecar echoes it from /api/status, which is
/// how the readiness check tells it from another workbench on the port
/// (wb#674). It distinguishes launches; it is not a secret.
fn new_launch_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{nanos:x}-{:x}", std::process::id())
}

/// Starts the bundled server. On failure, says which step failed and why, for
/// the startup dialog (wb#663).
fn start_sidecar(
    app: &AppHandle,
    launch_id: &str,
    exit: Arc<Mutex<SidecarExit>>,
) -> Result<(), String> {
    let state = app.state::<SidecarState>();
    let mut guard = state
        .child
        .lock()
        .map_err(|err| format!("Could not take the sidecar lock: {err}"))?;

    if guard.is_some() {
        return Ok(());
    }

    let command = app
        .shell()
        .sidecar(SIDECAR_NAME)
        .map_err(|err| format!("Could not find the bundled server ({SIDECAR_NAME}): {err}"))?;

    let (mut events, child) = match command
        .env("ANOLIS_WORKBENCH_OPEN_BROWSER", "0")
        .env("ANOLIS_WORKBENCH_HOST", WORKBENCH_HOST)
        .env("ANOLIS_WORKBENCH_PORT", WORKBENCH_PORT.to_string())
        .env("ANOLIS_WORKBENCH_LAUNCH_ID", launch_id)
        // The sidecar is a PyInstaller onefile bootloader that runs the server
        // as its own child, and kill() in stop_sidecar ends the bootloader
        // only. The server stops itself when the bootloader is gone (wb#667).
        // A process group would end the server too, but also any runtime the
        // server launched, by SIGKILL and before its safe-state shutdown; that
        // runtime keeps running when the app quits.
        .env("ANOLIS_WORKBENCH_EXIT_WITH_PARENT", "1")
        // The server also stops when this app is gone but the bootloader is
        // not (a crash), and when the bootloader died before its watch began.
        .env("ANOLIS_WORKBENCH_APP_PID", std::process::id().to_string())
        .args(["--no-browser"])
        .spawn()
    {
        Ok(result) => result,
        Err(err) => {
            return Err(format!(
                "Could not start the bundled server ({SIDECAR_NAME}): {err}"
            ))
        }
    };

    // Record how the sidecar ends, so startup can fail as soon as it does
    // rather than when the readiness wait times out (wb#674).
    let watcher = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            let Ok(mut exit) = exit.lock() else { return };
            match event {
                CommandEvent::Stderr(line) => {
                    let line = String::from_utf8_lossy(&line).trim_end().to_string();
                    if !line.is_empty() {
                        if exit.stderr_tail.len() == STDERR_TAIL_LINES {
                            exit.stderr_tail.pop_front();
                        }
                        exit.stderr_tail.push_back(line);
                    }
                }
                CommandEvent::Terminated(payload) => {
                    // A sidecar that has exited is not killed at quit: its pid
                    // may by then belong to another process.
                    if let Ok(mut child) = watcher.state::<SidecarState>().child.lock() {
                        child.take();
                    }
                    exit.ended = Some(match (payload.code, payload.signal) {
                        (Some(code), _) => format!("It exited with status {code}"),
                        (None, Some(signal)) => format!("It was ended by signal {signal}"),
                        (None, None) => "It exited".to_string(),
                    });
                }
                CommandEvent::Error(err) => {
                    exit.ended = Some(format!("Watching it failed: {err}"));
                }
                _ => {}
            }
        }
    });

    *guard = Some(child);
    Ok(())
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

/// Whether an HTTP response to GET /api/status comes from the sidecar this
/// launch started: a 200 whose `workbench.launch_id` is this launch's id.
/// Another workbench on the port answers with a different id or none (wb#674).
fn is_own_workbench(response: &str, launch_id: &str) -> bool {
    let status_ok = response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200");
    if !status_ok {
        return false;
    }
    let Some((_, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    let Ok(json) = serde_json::from_str::<serde_json::Value>(body) else {
        return false;
    };
    json.pointer("/workbench/launch_id")
        .and_then(serde_json::Value::as_str)
        == Some(launch_id)
}

/// Set once the user has agreed to quit with jobs running, so the exit the
/// dialog then requests is let through.
static QUIT_CONFIRMED: AtomicBool = AtomicBool::new(false);

/// Set while the quit confirmation is showing, so a second close does not
/// open a second one (review of #947).
static QUIT_DIALOG_OPEN: AtomicBool = AtomicBool::new(false);

/// This launch's id, which the sidecar echoes. The quit check asks only that
/// server: another workbench answering on the port runs no job of ours
/// (review of #947).
static LAUNCH_ID: OnceLock<String> = OnceLock::new();

/// The kinds of the server's running jobs, from a response to
/// GET /api/provision/active from the sidecar this launch started; empty for
/// anything else, including another workbench's answer.
fn running_job_kinds(response: &str, launch_id: &str) -> Vec<String> {
    let status_ok = response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200");
    let Some((_, body)) = response.split_once("\r\n\r\n").filter(|_| status_ok) else {
        return Vec::new();
    };
    let Ok(json) = serde_json::from_str::<serde_json::Value>(body) else {
        return Vec::new();
    };
    if json.get("launch_id").and_then(serde_json::Value::as_str) != Some(launch_id) {
        return Vec::new();
    }
    json.get("running")
        .and_then(serde_json::Value::as_array)
        .map(|jobs| {
            jobs.iter()
                .map(|job| {
                    job.get("kind")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("")
                        .to_string()
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Asks the server which jobs are still running. A server that does not
/// answer has nothing a quit could cut off that the app could name.
fn running_jobs_once() -> Vec<String> {
    let Some(launch_id) = LAUNCH_ID.get() else {
        return Vec::new();
    };
    let addr = SocketAddr::from(([127, 0, 0, 1], WORKBENCH_PORT));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return Vec::new();
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1000)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    let request = format!(
        "GET /api/provision/active HTTP/1.1\r\nHost: {WORKBENCH_HOST}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return Vec::new();
    }
    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return Vec::new();
    }
    running_job_kinds(&response, launch_id)
}

/// What the quit confirmation says about the jobs a quit would end.
fn quit_warning(kinds: &[String]) -> String {
    let deploys = kinds.iter().filter(|k| k.as_str() != "bundle").count();
    let exports = kinds.len() - deploys;
    let what = match (deploys, exports) {
        (0, _) => "A bundle export is still running".to_string(),
        (_, 0) => "A deploy is still running".to_string(),
        _ => "A deploy and a bundle export are still running".to_string(),
    };
    format!(
        "{what}. Quitting stops the workbench's server, and with it the job: \
a deploy stops partway on the target. Quit anyway?"
    )
}

/// Quits the app, first asking for confirmation when this launch's server
/// still runs a deploy or export: quitting ends the server, and since wb#933
/// its jobs with it. The server is asked off the main thread, so a slow one
/// does not freeze the window, and only one confirmation shows at a time
/// (review of #947).
fn request_quit(app: &AppHandle) {
    if QUIT_CONFIRMED.load(Ordering::SeqCst) {
        app.exit(0);
        return;
    }
    if QUIT_DIALOG_OPEN.load(Ordering::SeqCst) {
        return;
    }
    let handle = app.clone();
    thread::spawn(move || {
        let kinds = running_jobs_once();
        if kinds.is_empty() {
            QUIT_CONFIRMED.store(true, Ordering::SeqCst);
            handle.exit(0);
            return;
        }
        if QUIT_DIALOG_OPEN.swap(true, Ordering::SeqCst) {
            return;
        }
        let main = handle.clone();
        let _ = handle.run_on_main_thread(move || {
            let mut dialog = main
                .dialog()
                .message(quit_warning(&kinds))
                .title("Quit Anolis Workbench?")
                .buttons(MessageDialogButtons::OkCancelCustom(
                    "Quit anyway".to_string(),
                    "Keep running".to_string(),
                ))
                .kind(MessageDialogKind::Warning);
            if let Some(window) = main.get_webview_window("main") {
                dialog = dialog.parent(&window);
            }
            let exit = main.clone();
            dialog.show(move |quit| {
                QUIT_DIALOG_OPEN.store(false, Ordering::SeqCst);
                if quit {
                    QUIT_CONFIRMED.store(true, Ordering::SeqCst);
                    exit.exit(0);
                }
            });
        });
    });
}

/// The app menu on macOS: the default one, with a Quit item that goes
/// through `request_quit`. The default Quit sends `terminate:`, which tao
/// turns straight into an exit, so Cmd-Q and Dock > Quit skipped the
/// confirmation (review of #947). Built from the cross-platform menu API so
/// it compiles everywhere; installed on macOS only.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn app_menu_with_confirmed_quit(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let name = app.package_info().name.clone();
    let quit = MenuItem::with_id(
        app,
        QUIT_MENU_ID,
        format!("Quit {name}"),
        true,
        Some("CmdOrCtrl+Q"),
    )?;
    Menu::with_items(
        app,
        &[
            &Submenu::with_items(
                app,
                name,
                true,
                &[
                    &PredefinedMenuItem::about(app, None, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::services(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::hide(app, None)?,
                    &PredefinedMenuItem::hide_others(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &quit,
                ],
            )?,
            &Submenu::with_items(
                app,
                "Edit",
                true,
                &[
                    &PredefinedMenuItem::undo(app, None)?,
                    &PredefinedMenuItem::redo(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::cut(app, None)?,
                    &PredefinedMenuItem::copy(app, None)?,
                    &PredefinedMenuItem::paste(app, None)?,
                    &PredefinedMenuItem::select_all(app, None)?,
                ],
            )?,
            &Submenu::with_items(
                app,
                "View",
                true,
                &[&PredefinedMenuItem::fullscreen(app, None)?],
            )?,
            &Submenu::with_items(
                app,
                "Window",
                true,
                &[
                    &PredefinedMenuItem::minimize(app, None)?,
                    &PredefinedMenuItem::maximize(app, None)?,
                    &PredefinedMenuItem::separator(app)?,
                    &PredefinedMenuItem::close_window(app, None)?,
                ],
            )?,
        ],
    )
}

const QUIT_MENU_ID: &str = "anolis-quit";

fn check_health_once(launch_id: &str) -> bool {
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
    is_own_workbench(&response, launch_id)
}

#[derive(Debug, PartialEq)]
enum Startup {
    Ready,
    /// The sidecar ended before it was ready, with why.
    Exited(String),
    TimedOut,
}

/// Waits until `probe` passes, the sidecar has ended (`ended` returns why), or
/// `timeout` runs out. An ended sidecar is checked first on every round, so it
/// fails startup at once rather than at the timeout.
fn wait_for_startup(
    timeout: Duration,
    interval: Duration,
    mut probe: impl FnMut() -> bool,
    ended: impl Fn() -> Option<String>,
) -> Startup {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(reason) = ended() {
            return Startup::Exited(reason);
        }
        if probe() {
            return Startup::Ready;
        }
        if Instant::now() >= deadline {
            return Startup::TimedOut;
        }
        thread::sleep(interval);
    }
}

/// Shows why startup failed and exits with status 1 once the user dismisses
/// it. The dialog does not block, and the main thread must not, so the exit
/// happens in the dialog's callback rather than straight after showing it,
/// which closed the app before the dialog could be read (wb#674).
fn fail_startup(app: &AppHandle, message: String) {
    eprintln!("[desktop] {message}");
    for window in app.webview_windows().values() {
        let _ = window.hide();
    }
    let handle = app.clone();
    app.dialog()
        .message(message)
        .title("Anolis Workbench startup failed")
        .buttons(MessageDialogButtons::Ok)
        .kind(MessageDialogKind::Error)
        .show(move |_| {
            // This launch's server never started: nothing of ours to ask about
            // (review of #947).
            QUIT_CONFIRMED.store(true, Ordering::SeqCst);
            handle.exit(1);
        });
}

/// Builds the main window from tauri.conf.json, where it is not created at
/// startup, so it opens only on a server this launch started.
fn open_main_window(app: &AppHandle) -> Result<(), String> {
    let config = app
        .config()
        .app
        .windows
        .iter()
        .find(|w| w.label == "main")
        .ok_or("tauri.conf.json declares no \"main\" window")?;
    WebviewWindowBuilder::from_config(app, config)
        .and_then(|builder| builder.build())
        .map(|_| ())
        .map_err(|err| format!("Could not open the app window: {err}"))
}

fn startup_failure_message(outcome: &Startup) -> String {
    match outcome {
        Startup::Exited(reason) => format!(
            "Anolis Workbench's server stopped while starting.\n\n{reason}\n\n\
If another program is using port {WORKBENCH_PORT}, which the app needs, stop it \
and relaunch the app."
        ),
        _ => format!(
            "Anolis Workbench started, but it did not answer on port \
{WORKBENCH_PORT} within {} seconds.\n\n\
Another program may already be using port {WORKBENCH_PORT}, which the app \
needs. If so, stop it and relaunch the app.",
            STARTUP_TIMEOUT.as_secs()
        ),
    }
}

fn main() {
    #[cfg(target_os = "linux")]
    apply_linux_webview_workarounds();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            app.manage(SidecarState::default());

            let launch_id = new_launch_id();
            let _ = LAUNCH_ID.set(launch_id.clone());
            #[cfg(target_os = "macos")]
            app.set_menu(app_menu_with_confirmed_quit(app.handle())?)?;
            app.on_menu_event(|app, event| {
                if event.id() == QUIT_MENU_ID {
                    request_quit(app);
                }
            });
            let exit = Arc::new(Mutex::new(SidecarExit::default()));
            if let Err(reason) = start_sidecar(app.handle(), &launch_id, exit.clone()) {
                fail_startup(
                    app.handle(),
                    format!(
                        "Anolis Workbench could not start.\n\n{reason}\n\n\
The installation may be incomplete. Reinstalling the app may fix it."
                    ),
                );
                return Ok(());
            }

            // The wait runs off the main thread, so setup returns at once and
            // the event loop is not blocked while the sidecar starts (wb#674).
            let handle = app.handle().clone();
            thread::spawn(move || {
                let outcome = wait_for_startup(
                    STARTUP_TIMEOUT,
                    HEALTH_POLL_INTERVAL,
                    || check_health_once(&launch_id),
                    || exit.lock().ok().and_then(|e| e.reason()),
                );
                if outcome != Startup::Ready {
                    stop_sidecar(&handle);
                }
                let main_thread = handle.clone();
                let _ = handle.run_on_main_thread(move || {
                    let result = if outcome == Startup::Ready {
                        open_main_window(&main_thread)
                    } else {
                        Err(startup_failure_message(&outcome))
                    };
                    if let Err(message) = result {
                        fail_startup(&main_thread, message);
                    }
                });
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Anolis Workbench desktop shell")
        .run(|app_handle, event| match event {
            RunEvent::WindowEvent {
                event: WindowEvent::CloseRequested { api, .. },
                ..
            } => {
                if !QUIT_CONFIRMED.load(Ordering::SeqCst) {
                    api.prevent_close();
                    request_quit(app_handle);
                }
            }
            RunEvent::ExitRequested { api, .. } => {
                if QUIT_CONFIRMED.load(Ordering::SeqCst) {
                    stop_sidecar(app_handle);
                } else {
                    api.prevent_exit();
                    request_quit(app_handle);
                }
            }
            RunEvent::Exit => stop_sidecar(app_handle),
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn running_job_kinds_reads_the_active_route() {
        let body = r#"{"launch_id": "ours", "running": [{"job_id": "a", "kind": "remote"}, {"job_id": "b", "kind": "bundle"}]}"#;
        let ok = format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{body}");
        assert_eq!(running_job_kinds(&ok, "ours"), vec!["remote", "bundle"]);
        // Another workbench on the port: its jobs are not ours (review of #947).
        assert!(running_job_kinds(&ok, "another").is_empty());
        let unnamed = "HTTP/1.1 200 OK\r\n\r\n{\"running\": [{\"kind\": \"remote\"}]}";
        assert!(running_job_kinds(unnamed, "ours").is_empty());
        let none = "HTTP/1.1 200 OK\r\n\r\n{\"launch_id\": \"ours\", \"running\": []}";
        assert!(running_job_kinds(none, "ours").is_empty());
        assert!(running_job_kinds("HTTP/1.1 404 Not Found\r\n\r\n{}", "ours").is_empty());
        assert!(running_job_kinds("", "ours").is_empty());
    }

    #[test]
    fn quit_warning_names_what_would_stop() {
        assert!(quit_warning(&["remote".into()]).starts_with("A deploy is still running"));
        assert!(quit_warning(&["install".into()]).starts_with("A deploy is still running"));
        assert!(quit_warning(&["bundle".into()]).starts_with("A bundle export is still running"));
        assert!(quit_warning(&["remote".into(), "bundle".into()])
            .starts_with("A deploy and a bundle export"));
    }
    use std::cell::Cell;

    fn response(status: &str, body: &str) -> String {
        format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\n\r\n{body}")
    }

    #[test]
    fn accepts_only_the_sidecar_this_launch_started() {
        let own = r#"{"composer":{"port":3010},"workbench":{"version":1,"launch_id":"abc"}}"#;
        assert!(is_own_workbench(&response("200 OK", own), "abc"));
    }

    #[test]
    fn rejects_another_workbench_on_the_port() {
        // A workbench started some other way: browser mode, the service.
        let other = r#"{"composer":{"port":3010},"workbench":{"version":1,"launch_id":null}}"#;
        assert!(!is_own_workbench(&response("200 OK", other), "abc"));
        // A workbench from before launch_id existed.
        let older = r#"{"composer":{"port":3010},"workbench":{"version":1}}"#;
        assert!(!is_own_workbench(&response("200 OK", older), "abc"));
        // Another desktop launch's sidecar.
        let another = r#"{"workbench":{"version":1,"launch_id":"xyz"}}"#;
        assert!(!is_own_workbench(&response("200 OK", another), "abc"));
    }

    #[test]
    fn rejects_a_non_200_or_non_json_reply() {
        let own = r#"{"workbench":{"launch_id":"abc"}}"#;
        assert!(!is_own_workbench(&response("404 Not Found", own), "abc"));
        assert!(!is_own_workbench(&response("200 OK", "<html>"), "abc"));
    }

    #[test]
    fn a_sidecar_that_has_ended_fails_startup_at_once() {
        let start = Instant::now();
        let outcome = wait_for_startup(
            Duration::from_secs(20),
            Duration::from_millis(10),
            || false,
            || Some("It exited with status 1".to_string()),
        );
        assert_eq!(
            outcome,
            Startup::Exited("It exited with status 1".to_string())
        );
        assert!(start.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn a_sidecar_that_ends_while_starting_is_noticed_on_the_next_round() {
        let rounds = Cell::new(0);
        let outcome = wait_for_startup(
            Duration::from_secs(20),
            Duration::from_millis(1),
            || {
                rounds.set(rounds.get() + 1);
                false
            },
            || (rounds.get() >= 3).then(|| "It exited with status 1".to_string()),
        );
        assert_eq!(
            outcome,
            Startup::Exited("It exited with status 1".to_string())
        );
        assert_eq!(rounds.get(), 3);
    }

    #[test]
    fn ready_when_the_probe_passes() {
        let outcome = wait_for_startup(
            Duration::from_secs(20),
            Duration::from_millis(1),
            || true,
            || None,
        );
        assert_eq!(outcome, Startup::Ready);
    }

    #[test]
    fn times_out_when_nothing_answers_and_the_sidecar_runs_on() {
        let outcome = wait_for_startup(
            Duration::from_millis(30),
            Duration::from_millis(5),
            || false,
            || None,
        );
        assert_eq!(outcome, Startup::TimedOut);
    }

    #[test]
    fn an_exit_reason_quotes_the_sidecars_last_output() {
        let mut exit = SidecarExit::default();
        assert_eq!(exit.reason(), None);
        exit.stderr_tail
            .push_back("OSError: [Errno 98] Address already in use".to_string());
        exit.ended = Some("It exited with status 1".to_string());
        assert_eq!(
            exit.reason().as_deref(),
            Some("It exited with status 1. Its last output:\nOSError: [Errno 98] Address already in use")
        );
    }
}
