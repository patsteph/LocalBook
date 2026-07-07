//! macOS menu-bar tray companion (tray v1) — status + quick-launch.
//!
//! Additive to the existing shell: it reuses the backend health/lifecycle already
//! in lib.rs and polls `/system/tray-status` every 5s for the active models plus
//! tokens / avg throughput / avg response time. All navigation is routed to the
//! webview via a `tray-navigate` event (the frontend already owns opener/modal
//! handlers) so the Rust surface stays tiny and version-robust.

use std::time::Duration;

use serde::Deserialize;
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, Wry,
};

#[derive(Deserialize, Default)]
struct Models {
    #[serde(default)]
    main: String,
    #[serde(default)]
    fast: String,
    #[serde(default)]
    vision: String,
}
#[derive(Deserialize, Default)]
struct Metrics {
    #[serde(default)]
    tokens_in: u64,
    #[serde(default)]
    tokens_out: u64,
    #[serde(default)]
    tokens_per_sec: f64,
    #[serde(default)]
    avg_latency_ms: u64,
}
#[derive(Deserialize, Default)]
struct Enrich {
    #[serde(default)]
    queue_depth: u64,
}
#[derive(Deserialize, Default)]
struct Status {
    #[serde(default)]
    models: Models,
    #[serde(default)]
    metrics: Metrics,
    #[serde(default)]
    enrichment: Enrich,
}

pub(crate) fn init(app: &AppHandle) -> tauri::Result<()> {
    // Header rows (poll-updated). ENABLED so macOS renders them at full brightness
    // — disabled items are greyed/near-unreadable; clicking a header is a harmless
    // no-op (on_menu's default arm ignores these ids).
    let status = MenuItem::with_id(app, "status", "LocalBook — starting…", true, None::<&str>)?;
    let models = MenuItem::with_id(app, "models", "Models: …", true, None::<&str>)?;
    let models2 = MenuItem::with_id(app, "models2", "", true, None::<&str>)?;
    let metrics = MenuItem::with_id(app, "metrics", "Metrics: …", true, None::<&str>)?;
    let synth = MenuItem::with_id(app, "synth", "🧠 …", true, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Launch App", true, None::<&str>)?;
    let portal = MenuItem::with_id(app, "portal", "Health Portal", true, None::<&str>)?;
    let labs = MenuItem::with_id(app, "labs", "Labs (LLM)", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "🔄 Backend", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

    let sep1 = PredefinedMenuItem::separator(app)?;
    let sep2 = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[
            &status, &models, &models2, &metrics, &synth, &sep1, &open, &portal, &labs, &settings,
            &sep2, &restart, &quit,
        ],
    )?;

    let mut builder = TrayIconBuilder::with_id("localbook-tray")
        .menu(&menu)
        .tooltip("LocalBook")
        .on_menu_event(|app, event| on_menu(app, event.id.as_ref()));
    // Use the FULL-COLOR LocalBook icon (royal-blue book + grey pages). NOT a
    // template — template mode flattens it to monochrome. A colored icon renders
    // identically in light + dark menu bars (macOS doesn't invert it), and the
    // blue/purple outline reads on both.
    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon).icon_as_template(false);
    }
    builder.build(app)?;

    // Poll loop — /system/tray-status every 5s. DEBOUNCED: a single failed/slow poll
    // does NOT flip to red (the backend's event loop is briefly busy during LLM work
    // — a 2–9s stall shouldn't read as "dead"). Only ≥2 consecutive failures show
    // stopped; a lone blip keeps the last-good state.
    let (s, m, m2, me, sy) = (status.clone(), models.clone(), models2.clone(), metrics.clone(), synth.clone());
    tauri::async_runtime::spawn(async move {
        let client = reqwest::Client::new();
        let mut fails: u32 = 0;
        loop {
            match fetch(&client).await {
                Some(st) => {
                    fails = 0;
                    render_up(&s, &m, &m2, &me, &sy, &st);
                }
                None => {
                    fails += 1;
                    if fails >= 2 {
                        render_down(&s, &m, &m2, &me, &sy);
                    }
                    // else: single blip — leave the last-good state untouched.
                }
            }
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    });
    Ok(())
}

/// Fetch the status. Some(status) only on a real 2xx with a parseable body — a
/// 401/500 must NOT masquerade as running (serde(default) would parse an error
/// body into an all-zeros Status). None on any failure. Generous 10s timeout so a
/// transiently-busy backend isn't misreported.
async fn fetch(client: &reqwest::Client) -> Option<Status> {
    let r = client
        .get("http://localhost:8000/system/tray-status")
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .ok()?;
    if !r.status().is_success() {
        return None;
    }
    r.json::<Status>().await.ok()
}

fn render_up(
    status: &MenuItem<Wry>,
    models: &MenuItem<Wry>,
    models2: &MenuItem<Wry>,
    metrics: &MenuItem<Wry>,
    synth: &MenuItem<Wry>,
    st: &Status,
) {
    let _ = status.set_text("🟢 LocalBook running (:8000)");
    // Two lines keeps the menu narrow (was one very wide row).
    let _ = models.set_text(format!(
        "Main: {} · Vision: {}",
        short(&st.models.main),
        short(&st.models.vision)
    ));
    let _ = models2.set_text(format!("Fast: {}", short(&st.models.fast)));
    let total = st.metrics.tokens_in + st.metrics.tokens_out;
    let mut line = format!("{} tok · {:.0} tok/s", human(total), st.metrics.tokens_per_sec);
    if st.metrics.avg_latency_ms > 0 {
        line.push_str(&format!(" · {}", latency(st.metrics.avg_latency_ms)));
    }
    let _ = metrics.set_text(line);
    let _ = synth.set_text(if st.enrichment.queue_depth > 0 {
        format!("🧠 Synthesizing — {} in queue", st.enrichment.queue_depth)
    } else {
        "🧠 Idle".to_string()
    });
}

fn render_down(
    status: &MenuItem<Wry>,
    models: &MenuItem<Wry>,
    models2: &MenuItem<Wry>,
    metrics: &MenuItem<Wry>,
    synth: &MenuItem<Wry>,
) {
    let _ = status.set_text("🔴 LocalBook — backend stopped");
    let _ = models.set_text("Main: —");
    let _ = models2.set_text("");
    let _ = metrics.set_text("");
    let _ = synth.set_text("");
}

fn on_menu(app: &AppHandle, id: &str) {
    match id {
        "open" => show_main(app),
        // Route into the webview's existing handlers (opener/modals).
        "labs" | "settings" | "portal" => {
            show_main(app);
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.emit("tray-navigate", id.to_string());
            }
        }
        "restart" => crate::restart_backend_from_tray(app),
        "quit" => app.exit(0),
        _ => {}
    }
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn short(m: &str) -> String {
    if m.is_empty() {
        "—".into()
    } else {
        m.split(':').next().unwrap_or(m).to_string()
    }
}

fn human(n: u64) -> String {
    if n >= 1_000_000 {
        format!("{:.1}M", n as f64 / 1e6)
    } else if n >= 1_000 {
        format!("{:.0}k", n as f64 / 1e3)
    } else {
        n.to_string()
    }
}

// Match the Health Portal's latency formatting (seconds once past 1s).
fn latency(ms: u64) -> String {
    if ms >= 1000 {
        format!("{:.1}s avg", ms as f64 / 1000.0)
    } else {
        format!("{}ms avg", ms)
    }
}
