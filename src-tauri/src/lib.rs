use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Manager};
use std::time::Duration;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

// State to track the backend process
struct BackendState {
    process: Arc<Mutex<Option<CommandChild>>>,
    ready: Arc<Mutex<bool>>,
}

// Tauri command to check if backend is ready
#[tauri::command]
async fn is_backend_ready(state: tauri::State<'_, BackendState>) -> Result<bool, String> {
    let ready = state.ready.lock().map_err(|e| e.to_string())?;
    Ok(*ready)
}

// Tauri command to check backend health
#[tauri::command]
async fn check_backend_health() -> Result<bool, String> {
    match check_health().await {
        Ok(healthy) => Ok(healthy),
        Err(e) => {
            eprintln!("Health check failed: {}", e);
            Ok(false)
        }
    }
}

// Function to check backend health
async fn check_health() -> Result<bool, Box<dyn std::error::Error>> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()?;

    let response = client
        .get("http://localhost:8000/health")
        .send()
        .await?;

    Ok(response.status().is_success())
}

// Function to start the backend sidecar
async fn start_backend(app_handle: &AppHandle) -> Result<Option<CommandChild>, String> {
    println!("Attempting to start backend sidecar...");

    // Try to get the sidecar command
    match app_handle.shell().sidecar("localbook-backend") {
        Ok(sidecar_command) => {
            // Spawn the sidecar process
            match sidecar_command.spawn() {
                Ok((mut rx, child)) => {
                    println!("Backend sidecar started successfully");

                    // Log output in background
                    tauri::async_runtime::spawn(async move {
                        while let Some(event) = rx.recv().await {
                            match event {
                                tauri_plugin_shell::process::CommandEvent::Stdout(line) => {
                                    println!("[Backend] {}", String::from_utf8_lossy(&line));
                                }
                                tauri_plugin_shell::process::CommandEvent::Stderr(line) => {
                                    eprintln!("[Backend] {}", String::from_utf8_lossy(&line));
                                }
                                tauri_plugin_shell::process::CommandEvent::Error(err) => {
                                    eprintln!("[Backend Error] {}", err);
                                }
                                tauri_plugin_shell::process::CommandEvent::Terminated(payload) => {
                                    println!("[Backend] Process terminated with code: {:?}", payload.code);
                                }
                                _ => {}
                            }
                        }
                    });

                    Ok(Some(child))
                }
                Err(e) => {
                    println!("Sidecar not available: {}", e);
                    println!("Running in dev mode - backend should be started externally");
                    Ok(None)
                }
            }
        }
        Err(e) => {
            println!("Sidecar not found: {} - running in dev mode", e);
            Ok(None)
        }
    }
}

// Function to wait for backend to be ready
async fn wait_for_backend_ready(max_attempts: u32) -> Result<(), Box<dyn std::error::Error>> {
    println!("Waiting for backend to be ready...");

    for attempt in 1..=max_attempts {
        tokio::time::sleep(Duration::from_secs(1)).await;

        match check_health().await {
            Ok(true) => {
                println!("Backend is ready!");
                return Ok(());
            }
            Ok(false) => {
                println!("Attempt {}/{}: Backend not healthy yet", attempt, max_attempts);
            }
            Err(e) => {
                println!("Attempt {}/{}: {}", attempt, max_attempts, e);
            }
        }
    }

    Err("Backend failed to start within timeout".into())
}

// Setup function to initialize backend on app startup
fn setup_backend(app: &AppHandle) -> Result<BackendState, String> {
    let state = BackendState {
        process: Arc::new(Mutex::new(None)),
        ready: Arc::new(Mutex::new(false)),
    };

    let app_handle = app.clone();
    let process_ref = state.process.clone();
    let ready_ref = state.ready.clone();

    // Spawn backend startup in background
    tauri::async_runtime::spawn(async move {
        match start_backend(&app_handle).await {
            Ok(child_opt) => {
                if let Some(child) = child_opt {
                    println!("Backend sidecar process started");
                    if let Ok(mut process) = process_ref.lock() {
                        *process = Some(child);
                    }
                } else {
                    println!("Backend running externally (dev mode)");
                }

                // Wait for backend to be ready
                match wait_for_backend_ready(30).await {
                    Ok(_) => {
                        if let Ok(mut ready) = ready_ref.lock() {
                            *ready = true;
                        }
                        println!("Backend initialization complete");
                    }
                    Err(e) => {
                        eprintln!("Failed to connect to backend: {}", e);
                        eprintln!("");
                        eprintln!("Please ensure the backend is running.");
                        eprintln!("For dev mode: ./start.sh");
                    }
                }
            }
            Err(e) => {
                eprintln!("Failed to start backend: {}", e);
            }
        }
    });

    Ok(state)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .setup(|app| {
            let backend_state = setup_backend(&app.handle())?;
            app.manage(backend_state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            is_backend_ready,
            check_backend_health
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
