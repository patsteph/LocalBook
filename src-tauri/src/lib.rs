use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager};
use std::time::Duration;
use std::path::PathBuf;
use serde::Serialize;

// State to track the backend process
struct BackendState {
    process: Arc<Mutex<Option<std::process::Child>>>,
    ready: Arc<Mutex<bool>>,
    status: Arc<Mutex<BackendStatus>>,
}

#[derive(Clone, Serialize)]
struct BackendStatus {
    stage: String,
    message: String,
    last_error: Option<String>,
}

// Tauri command to check if backend is ready
#[tauri::command]
async fn is_backend_ready(state: tauri::State<'_, BackendState>) -> Result<bool, String> {
    let ready = state.ready.lock().map_err(|e| e.to_string())?;
    Ok(*ready)
}

#[tauri::command]
async fn get_backend_status(state: tauri::State<'_, BackendState>) -> Result<BackendStatus, String> {
    let status = state.status.lock().map_err(|e| e.to_string())?;
    Ok(status.clone())
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
        .timeout(Duration::from_secs(5))
        .build()?;

    let response = client
        .get("http://localhost:8000/health")
        .send()
        .await?;

    Ok(response.status().is_success())
}

// Function to check if Ollama is running
async fn check_ollama() -> bool {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .build();
    
    match client {
        Ok(c) => c.get("http://localhost:11434/api/tags").send().await.is_ok(),
        Err(_) => false,
    }
}

// Function to check if a model is available in Ollama
async fn check_model_available(model_name: &str) -> bool {
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build() {
            Ok(c) => c,
            Err(_) => return false,
        };
    
    let response = client
        .get("http://localhost:11434/api/tags")
        .send()
        .await;
    
    match response {
        Ok(resp) => {
            if let Ok(text) = resp.text().await {
                // Check if model name appears in the response
                text.contains(model_name)
            } else {
                false
            }
        }
        Err(_) => false,
    }
}

// Function to pull a model from Ollama
async fn pull_ollama_model(model_name: &str) -> Result<(), String> {
    println!("Pulling Ollama model: {}", model_name);
    
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(600)) // 10 min timeout for large models
        .build()
        .map_err(|e| format!("Failed to create client: {}", e))?;
    
    let response = client
        .post("http://localhost:11434/api/pull")
        .json(&serde_json::json!({
            "name": model_name,
            "stream": false
        }))
        .send()
        .await
        .map_err(|e| format!("Failed to pull model: {}", e))?;
    
    if response.status().is_success() {
        println!("Successfully pulled model: {}", model_name);
        Ok(())
    } else {
        Err(format!("Failed to pull model {}: HTTP {}", model_name, response.status()))
    }
}

// Required models for LocalBook - must match backend/config.py settings
const REQUIRED_MODELS: &[(&str, &str)] = &[
    ("olmo-3:7b-instruct", "Main AI model (~4.5GB)"),
    ("phi4-mini:latest", "Fast AI model (~2.5GB)"),
    ("snowflake-arctic-embed2", "Embedding model (~1.2GB)"),
];

// Function to ensure all required models are available
async fn ensure_required_models(status_ref: &Arc<Mutex<BackendStatus>>) {
    println!("Checking required AI models...");
    
    for (model_name, description) in REQUIRED_MODELS {
        if let Ok(mut status) = status_ref.lock() {
            status.stage = "checking_models".to_string();
            status.message = format!("Checking {}...", description);
        }
        
        if !check_model_available(model_name).await {
            println!("Model {} not found, downloading...", model_name);
            
            if let Ok(mut status) = status_ref.lock() {
                status.stage = "downloading_model".to_string();
                status.message = format!("Downloading {} (this may take several minutes)...", description);
            }
            
            match pull_ollama_model(model_name).await {
                Ok(_) => {
                    println!("Model {} downloaded successfully", model_name);
                }
                Err(e) => {
                    eprintln!("Failed to download model {}: {}", model_name, e);
                    if let Ok(mut status) = status_ref.lock() {
                        status.last_error = Some(format!("Failed to download {}: {}", model_name, e));
                    }
                }
            }
        } else {
            println!("Model {} is available", model_name);
        }
    }
    
    println!("Model check complete");
}

// Function to start Ollama if not running
async fn ensure_ollama_running() {
    if check_ollama().await {
        println!("Ollama is already running");
        return;
    }

    println!("Starting Ollama...");
    
    // Try common Ollama installation paths
    let ollama_paths = [
        "/opt/homebrew/bin/ollama",  // Apple Silicon Homebrew
        "/usr/local/bin/ollama",      // Intel Homebrew
        "/Applications/Ollama.app/Contents/Resources/ollama", // Ollama.app
        "ollama",                      // Fallback to PATH
    ];

    let mut result = None;
    for path in &ollama_paths {
        let attempt = std::process::Command::new(path)
            .arg("serve")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
        
        if attempt.is_ok() {
            result = Some(attempt);
            println!("Started Ollama from: {}", path);
            break;
        }
    }

    let result = result.unwrap_or_else(|| {
        std::process::Command::new("ollama")
            .arg("serve")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
    });

    match result {
        Ok(_) => {
            // Wait for Ollama to be ready
            for attempt in 1..=10 {
                tokio::time::sleep(Duration::from_secs(1)).await;
                if check_ollama().await {
                    println!("Ollama started successfully");
                    return;
                }
                println!("Waiting for Ollama... attempt {}/10", attempt);
            }
            eprintln!("Warning: Ollama may not have started properly");
        }
        Err(e) => {
            eprintln!("Could not start Ollama: {}", e);
            eprintln!("Please start Ollama manually: ollama serve");
        }
    }
}

// Function to kill any existing backend process
fn kill_existing_backend() {
    // Kill anything on port 8000 AND any localbook-backend processes.
    // Port-based kill catches dev-mode (python -m uvicorn) AND bundled processes.
    #[cfg(unix)]
    {
        // 1. Kill by process name (bundled backend)
        let _ = std::process::Command::new("pkill")
            .args(["-f", "localbook-backend"])
            .output();

        // 2. Kill by port (catches dev-mode python, orphaned processes, etc.)
        //    lsof -t -i:8000 returns PIDs; kill sends SIGTERM to each
        if let Ok(output) = std::process::Command::new("lsof")
            .args(["-t", "-i:8000"])
            .output()
        {
            let pids = String::from_utf8_lossy(&output.stdout);
            for pid in pids.split_whitespace() {
                let _ = std::process::Command::new("kill")
                    .arg(pid)
                    .output();
            }
        }

        // Wait for graceful shutdown (3s is enough for DB flush + model save)
        std::thread::sleep(Duration::from_secs(3));

        // 3. Force-kill stragglers on port 8000
        if let Ok(output) = std::process::Command::new("lsof")
            .args(["-t", "-i:8000"])
            .output()
        {
            let pids = String::from_utf8_lossy(&output.stdout);
            for pid in pids.split_whitespace() {
                let _ = std::process::Command::new("kill")
                    .args(["-9", pid])
                    .output();
            }
        }

        // SIGKILL any localbook-backend stragglers too
        let _ = std::process::Command::new("pkill")
            .args(["-9", "-f", "localbook-backend"])
            .output();

        // Give it a moment to release the port
        std::thread::sleep(Duration::from_millis(500));
    }
}

// Function to start the backend from resources
async fn start_backend(app_handle: &AppHandle) -> Result<Option<std::process::Child>, String> {
    println!("Attempting to start backend...");
    
    // Kill any existing backend first to avoid port conflicts
    kill_existing_backend();

    let resource_dir = app_handle
        .path()
        .resource_dir()
        .map_err(|e| format!("Failed to get resource dir: {}", e))?;

    let backend_exe_name = if cfg!(target_os = "windows") {
        "localbook-backend.exe"
    } else {
        "localbook-backend"
    };

    // In a packaged app, resource_dir() already points to the platform's Resources folder.
    // Depending on bundling/layout, resources may land at either:
    //   <resource_dir>/backend/localbook-backend/<exe>
    // or (older/alternative layout):
    //   <resource_dir>/resources/backend/localbook-backend/<exe>
    let candidate_paths: Vec<PathBuf> = vec![
        resource_dir
            .join("backend")
            .join("localbook-backend")
            .join(backend_exe_name),
        resource_dir
            .join("resources")
            .join("backend")
            .join("localbook-backend")
            .join(backend_exe_name),
    ];

    for candidate in candidate_paths {
        println!("Looking for backend at: {:?}", candidate);
        if !candidate.exists() {
            continue;
        }

        println!("Starting bundled backend...");
        let backend_dir = candidate
            .parent()
            .ok_or_else(|| "Backend path has no parent directory".to_string())?;
        println!("Backend working directory: {:?}", backend_dir);

        return match std::process::Command::new(&candidate)
            .current_dir(backend_dir)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::inherit())
            .stderr(std::process::Stdio::inherit())
            .spawn()
        {
            Ok(child) => {
                println!("Backend spawned with PID: {:?}", child.id());
                Ok(Some(child))
            }
            Err(e) => {
                eprintln!("Failed to start backend: {}", e);
                Err(format!("Failed to start backend: {}", e))
            }
        };
    }

    // Dev mode: backend should be started externally via start.sh
    println!("Bundled backend not found in resource dir: {:?}", resource_dir);
    println!("Running in dev mode - backend should be started externally");
    Ok(None)
}

// Function to wait for backend to be ready
async fn wait_for_backend_ready(max_attempts: u32) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
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

// ── Backend Watchdog ──────────────────────────────────────────────────────────
// Two-tier health monitoring (industry best practice, adapted from K8s probes):
//
//   Tier 1 — PID check: Is the managed process still alive?
//            If NO  → process crashed → restart immediately (fast recovery)
//            If YES → proceed to Tier 2
//
//   Tier 2 — HTTP liveness: Can it respond to /health?
//            If YES → healthy, reset counters
//            If NO  → process alive but slow (memory pressure) → be patient
//                     Only restart after HTTP_FAIL_THRESHOLD consecutive failures
//
// This prevents the death spiral where a slow-but-alive backend under memory
// pressure gets killed, restarted, and killed again in a tight loop.
//
// On confirmed crash:
//   1. Logs to backend_crashes.log (+ checks macOS DiagnosticReports)
//   2. Emits "backend-health" Tauri event to the frontend
//   3. Attempts silent restart with exponential backoff (up to MAX_RESTARTS)

/// Check whether the managed backend process is still running.
/// Returns true if the process is alive, false if it has exited or we don't
/// have a tracked process (dev mode).
fn is_process_alive(process_ref: &Arc<Mutex<Option<std::process::Child>>>) -> Option<bool> {
    if let Ok(mut guard) = process_ref.lock() {
        if let Some(ref mut child) = *guard {
            // try_wait: Ok(Some(_)) = exited, Ok(None) = still running, Err = unknown
            match child.try_wait() {
                Ok(Some(_status)) => Some(false), // Process exited
                Ok(None) => Some(true),            // Still running
                Err(_) => Some(true),              // Assume alive on error (safe default)
            }
        } else {
            None // No tracked process (dev mode) — skip PID checks
        }
    } else {
        None // Mutex poisoned — skip PID checks
    }
}

async fn backend_watchdog(
    app_handle: AppHandle,
    process_ref: Arc<Mutex<Option<std::process::Child>>>,
    ready_ref: Arc<Mutex<bool>>,
    status_ref: Arc<Mutex<BackendStatus>>,
) {
    // ── Tuning constants (K8s best practices for memory-pressure-prone apps) ──
    //
    // LIVENESS_INTERVAL: 15s — K8s recommends 15-30s for liveness probes
    // LIVENESS_TIMEOUT:   5s — K8s recommends 3-5s; generous for memory pressure
    // HTTP_FAIL_THRESHOLD: 8 — 8 × 15s = 120s of unresponsiveness before restart
    //                          (K8s default is 3; we're generous because our app
    //                          legitimately goes slow under Ollama memory pressure)
    // PROCESS_DEAD_CONFIRMS: 2 — confirm PID gone twice to avoid race conditions
    // STARTUP_GRACE_SECS:  90 — model warmup + background tasks need time
    // RESTART_GRACE_SECS:  90 — same grace period after a restart recovery
    // MAX_RESTARTS:         5 — with exponential backoff between attempts

    const LIVENESS_INTERVAL: Duration = Duration::from_secs(15);
    const HTTP_FAIL_THRESHOLD: u32 = 8;
    const PROCESS_DEAD_CONFIRMS: u32 = 2;
    const STARTUP_GRACE_SECS: u64 = 90;
    const RESTART_GRACE_SECS: u64 = 90;
    const MAX_RESTARTS: u32 = 5;

    let mut http_failures: u32 = 0;
    let mut pid_dead_count: u32 = 0;
    let mut restart_count: u32 = 0;

    // Wait for initial startup to complete before monitoring
    loop {
        tokio::time::sleep(Duration::from_secs(2)).await;
        if let Ok(ready) = ready_ref.lock() {
            if *ready {
                break;
            }
        }
    }

    println!("[Watchdog] Backend health monitoring active");

    // Startup grace period — backend startup is resource-intensive
    // (model warmup, KG extraction, memory scheduler, etc.)
    tokio::time::sleep(Duration::from_secs(STARTUP_GRACE_SECS)).await;
    println!("[Watchdog] Startup grace period ({}s) complete — monitoring started", STARTUP_GRACE_SECS);

    loop {
        tokio::time::sleep(LIVENESS_INTERVAL).await;

        // ── Tier 1: PID check — is the process still alive? ──
        let pid_status = is_process_alive(&process_ref);

        if pid_status == Some(false) {
            // Process has exited — this is a real crash
            pid_dead_count += 1;
            println!(
                "[Watchdog] Process exited! (confirm {}/{})",
                pid_dead_count, PROCESS_DEAD_CONFIRMS
            );

            if pid_dead_count >= PROCESS_DEAD_CONFIRMS {
                // Confirmed dead — skip HTTP checks, go straight to restart
                println!("[Watchdog] Process confirmed dead — initiating restart");
                http_failures = 0;
                pid_dead_count = 0;
                // Fall through to restart logic below
            } else {
                continue; // Wait for confirmation
            }
        } else {
            // Process is alive (or dev mode) — reset PID counter
            pid_dead_count = 0;

            // ── Tier 2: HTTP liveness — can it respond? ──
            let healthy = check_health().await.unwrap_or(false);

            if healthy {
                if http_failures > 0 {
                    println!(
                        "[Watchdog] Backend responsive after {} slow check(s) — healthy",
                        http_failures
                    );
                }
                http_failures = 0;
                continue; // All good
            }

            // Process alive but HTTP failed — likely slow under memory pressure
            http_failures += 1;
            println!(
                "[Watchdog] HTTP liveness failed ({}/{}) — process alive, likely under pressure",
                http_failures, HTTP_FAIL_THRESHOLD
            );

            if http_failures < HTTP_FAIL_THRESHOLD {
                continue; // Be patient — process is alive, just slow
            }

            // Exhausted patience — process alive but unresponsive for 2+ minutes
            println!(
                "[Watchdog] Backend unresponsive for {}s — initiating restart",
                http_failures as u64 * LIVENESS_INTERVAL.as_secs()
            );
            http_failures = 0;
        }

        // ── Backend needs restart ──
        log_crash_to_file(&app_handle, restart_count);

        if let Ok(mut ready) = ready_ref.lock() {
            *ready = false;
        }
        if let Ok(mut status) = status_ref.lock() {
            status.stage = "crashed".to_string();
            status.message = "Backend stopped unexpectedly. Restarting...".to_string();
            status.last_error =
                Some("Backend process stopped unexpectedly".to_string());
        }

        let _ = app_handle.emit(
            "backend-health",
            serde_json::json!({
                "status": "crashed",
                "restart_attempt": restart_count + 1,
                "max_restarts": MAX_RESTARTS,
                "message": "Backend stopped unexpectedly. Restarting..."
            }),
        );

        if restart_count >= MAX_RESTARTS {
            println!(
                "[Watchdog] Max restarts ({}) reached — stopping watchdog",
                MAX_RESTARTS
            );
            if let Ok(mut status) = status_ref.lock() {
                status.stage = "error".to_string();
                status.message = format!(
                    "Backend crashed {} times. Please restart the application.",
                    MAX_RESTARTS
                );
            }
            let _ = app_handle.emit(
                "backend-health",
                serde_json::json!({
                    "status": "failed",
                    "message": format!("Backend has crashed {} times. Please restart LocalBook.", MAX_RESTARTS)
                }),
            );
            break;
        }

        // ── Attempt restart with exponential backoff ──
        restart_count += 1;
        println!(
            "[Watchdog] Restart attempt {}/{}",
            restart_count, MAX_RESTARTS
        );

        if let Ok(mut status) = status_ref.lock() {
            status.stage = "restarting".to_string();
            status.message = format!(
                "Restarting backend (attempt {}/{})...",
                restart_count, MAX_RESTARTS
            );
        }
        let _ = app_handle.emit(
            "backend-health",
            serde_json::json!({
                "status": "restarting",
                "restart_attempt": restart_count,
                "max_restarts": MAX_RESTARTS,
                "message": format!("Restarting backend (attempt {}/{})...", restart_count, MAX_RESTARTS)
            }),
        );

        // Exponential backoff: 5s, 10s, 20s, 40s, 80s
        // Gives macOS time to free memory between restart attempts
        let backoff_secs = 5u64 * 2u64.pow(restart_count.saturating_sub(1));
        println!(
            "[Watchdog] Waiting {}s before restart (backoff)...",
            backoff_secs
        );
        tokio::time::sleep(Duration::from_secs(backoff_secs)).await;

        match start_backend(&app_handle).await {
            Ok(child_opt) => {
                if let Some(child) = child_opt {
                    if let Ok(mut process) = process_ref.lock() {
                        *process = Some(child);
                    }
                }

                match wait_for_backend_ready(30).await {
                    Ok(_) => {
                        println!(
                            "[Watchdog] Backend recovered (restart #{})",
                            restart_count
                        );
                        if let Ok(mut ready) = ready_ref.lock() {
                            *ready = true;
                        }
                        if let Ok(mut status) = status_ref.lock() {
                            status.stage = "ready".to_string();
                            status.message = "Backend ready".to_string();
                            status.last_error = None;
                        }
                        let _ = app_handle.emit(
                            "backend-health",
                            serde_json::json!({
                                "status": "recovered",
                                "restart_count": restart_count,
                                "message": "Backend recovered successfully"
                            }),
                        );

                        // Post-restart grace period — startup tasks are
                        // resource-intensive (model warmup, KG extraction)
                        println!(
                            "[Watchdog] Post-restart grace period ({}s)...",
                            RESTART_GRACE_SECS
                        );
                        tokio::time::sleep(Duration::from_secs(RESTART_GRACE_SECS)).await;
                    }
                    Err(e) => {
                        println!("[Watchdog] Backend failed to recover: {}", e);
                        // Will loop and try again on next iteration
                    }
                }
            }
            Err(e) => {
                println!("[Watchdog] Failed to restart backend: {}", e);
            }
        }
    }
}

fn log_crash_to_file(app_handle: &AppHandle, restart_count: u32) {
    if let Ok(data_dir) = app_handle.path().app_data_dir() {
        let log_path = data_dir.join("backend_crashes.log");
        // Use Unix timestamp — keeps it simple without chrono dependency
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();

        let mut entry = format!(
            "[ts={}] Backend crash detected (restart attempt #{})\n",
            ts,
            restart_count + 1
        );

        // Layer 3: Check macOS DiagnosticReports for native crash info
        // macOS writes crash reports here for SIGKILL/SIGSEGV/SIGABRT
        if let Some(home) = std::env::var_os("HOME") {
            let diag_dir = std::path::Path::new(&home)
                .join("Library/Logs/DiagnosticReports");
            if diag_dir.exists() {
                // Look for recent localbook-backend crash reports (last 120 seconds)
                if let Ok(entries) = std::fs::read_dir(&diag_dir) {
                    let cutoff = std::time::SystemTime::now()
                        - Duration::from_secs(120);
                    for e in entries.flatten() {
                        let name = e.file_name().to_string_lossy().to_string();
                        if !name.contains("localbook-backend") {
                            continue;
                        }
                        if let Ok(meta) = e.metadata() {
                            if let Ok(modified) = meta.modified() {
                                if modified > cutoff {
                                    // Found a recent crash report — extract key lines
                                    entry.push_str(&format!(
                                        "  macOS crash report: {}\n", name
                                    ));
                                    if let Ok(content) = std::fs::read_to_string(e.path()) {
                                        // Extract Exception Type and Termination Reason
                                        for line in content.lines().take(80) {
                                            let l = line.trim();
                                            if l.starts_with("Exception Type:")
                                                || l.starts_with("Termination Reason:")
                                                || l.starts_with("Termination Signal:")
                                                || l.starts_with("VM Region Info:")
                                            {
                                                entry.push_str(&format!(
                                                    "  {}\n", l
                                                ));
                                            }
                                        }
                                    }
                                    println!(
                                        "[Watchdog] Found macOS crash report: {}", name
                                    );
                                }
                            }
                        }
                    }
                }
            }
        }

        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
        {
            let _ = std::io::Write::write_all(&mut f, entry.as_bytes());
            println!("[Watchdog] Crash logged to {:?}", log_path);
        }
    }
}

// Setup function to initialize backend on app startup
fn setup_backend(app: &AppHandle) -> Result<BackendState, String> {
    let state = BackendState {
        process: Arc::new(Mutex::new(None)),
        ready: Arc::new(Mutex::new(false)),
        status: Arc::new(Mutex::new(BackendStatus {
            stage: "starting".to_string(),
            message: "Initializing backend services...".to_string(),
            last_error: None,
        })),
    };

    let app_handle = app.clone();
    let process_ref = state.process.clone();
    let ready_ref = state.ready.clone();
    let status_ref = state.status.clone();

    // Clone refs for the watchdog task
    let wd_app = app.clone();
    let wd_process = state.process.clone();
    let wd_ready = state.ready.clone();
    let wd_status = state.status.clone();

    // Spawn backend startup in background
    tauri::async_runtime::spawn(async move {
        if let Ok(mut status) = status_ref.lock() {
            status.stage = "starting_ollama".to_string();
            status.message = "Starting Ollama...".to_string();
            status.last_error = None;
        }
        // Ensure Ollama is running first
        ensure_ollama_running().await;

        // Check and download required models
        ensure_required_models(&status_ref).await;

        if let Ok(mut status) = status_ref.lock() {
            status.stage = "starting_backend".to_string();
            status.message = "Starting backend...".to_string();
        }

        match start_backend(&app_handle).await {
            Ok(child_opt) => {
                if let Some(child) = child_opt {
                    println!("Backend process started");
                    if let Ok(mut process) = process_ref.lock() {
                        *process = Some(child);
                    }
                } else {
                    println!("Backend running externally (dev mode)");
                }

                if let Ok(mut status) = status_ref.lock() {
                    status.stage = "waiting_for_backend".to_string();
                    status.message = "Waiting for backend to be ready...".to_string();
                }

                // Wait for backend to be ready
                match wait_for_backend_ready(30).await {
                    Ok(_) => {
                        if let Ok(mut ready) = ready_ref.lock() {
                            *ready = true;
                        }
                        if let Ok(mut status) = status_ref.lock() {
                            status.stage = "ready".to_string();
                            status.message = "Backend ready".to_string();
                            status.last_error = None;
                        }
                        println!("Backend initialization complete");
                    }
                    Err(e) => {
                        eprintln!("Failed to connect to backend: {}", e);
                        eprintln!("");
                        eprintln!("Please ensure the backend is running.");
                        eprintln!("For dev mode: ./start.sh");
                        if let Ok(mut status) = status_ref.lock() {
                            status.stage = "error".to_string();
                            status.message = "Backend failed to start".to_string();
                            status.last_error = Some(e.to_string());
                        }
                    }
                }
            }
            Err(e) => {
                eprintln!("Failed to start backend: {}", e);
                if let Ok(mut status) = status_ref.lock() {
                    status.stage = "error".to_string();
                    status.message = "Backend failed to start".to_string();
                    status.last_error = Some(e);
                }
            }
        }
    });

    // Spawn watchdog — waits for ready=true, then monitors continuously
    tauri::async_runtime::spawn(async move {
        backend_watchdog(wd_app, wd_process, wd_ready, wd_status).await;
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
        .plugin(tauri_plugin_process::init())
        .setup(|app| {
            let backend_state = setup_backend(&app.handle())?;
            app.manage(backend_state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            is_backend_ready,
            check_backend_health,
            get_backend_status
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                println!("[Shutdown] Cleaning up backend process...");
                kill_existing_backend();
                println!("[Shutdown] Backend cleanup complete");
            }
        });
}
