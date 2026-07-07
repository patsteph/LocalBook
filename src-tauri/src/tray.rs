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
    vision: String,
}
#[derive(Deserialize, Default)]
struct Metrics {
    #[serde(default)]
    total_tokens: u64,
    #[serde(default)]
    tokens_per_sec: f64,
    #[serde(default)]
    avg_response_ms: u64,
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
    // Disabled header rows (poll-updated) + action rows.
    let status = MenuItem::with_id(app, "status", "LocalBook — starting…", false, None::<&str>)?;
    let models = MenuItem::with_id(app, "models", "Models: …", false, None::<&str>)?;
    let metrics = MenuItem::with_id(app, "metrics", "Metrics: …", false, None::<&str>)?;
    let synth = MenuItem::with_id(app, "synth", "🧠 …", false, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Open LocalBook", true, None::<&str>)?;
    let portal = MenuItem::with_id(app, "portal", "Open Health Portal", true, None::<&str>)?;
    let labs = MenuItem::with_id(app, "labs", "LLM Locker / Evaluator (Labs)", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "Restart Backend", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit LocalBook", true, None::<&str>)?;

    let sep1 = PredefinedMenuItem::separator(app)?;
    let sep2 = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[
            &status, &models, &metrics, &synth, &sep1, &open, &portal, &labs, &settings, &sep2,
            &restart, &quit,
        ],
    )?;

    let mut builder = TrayIconBuilder::with_id("localbook-tray")
        .menu(&menu)
        .tooltip("LocalBook")
        .on_menu_event(|app, event| on_menu(app, event.id.as_ref()));
    // Reuse the app icon as a template image (macOS tints it for light/dark).
    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon).icon_as_template(true);
    }
    builder.build(app)?;

    // Poll loop — one cheap /system/tray-status call updates the header rows.
    let (s, m, me, sy) = (status.clone(), models.clone(), metrics.clone(), synth.clone());
    tauri::async_runtime::spawn(async move {
        let client = reqwest::Client::new();
        loop {
            update(&client, &s, &m, &me, &sy).await;
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    });
    Ok(())
}

async fn update(
    client: &reqwest::Client,
    status: &MenuItem<Wry>,
    models: &MenuItem<Wry>,
    metrics: &MenuItem<Wry>,
    synth: &MenuItem<Wry>,
) {
    let resp = client
        .get("http://localhost:8000/system/tray-status")
        .timeout(Duration::from_secs(4))
        .send()
        .await;
    if let Ok(r) = resp {
        if let Ok(st) = r.json::<Status>().await {
            let _ = status.set_text("● LocalBook — running (:8000)");
            let _ = models.set_text(format!(
                "Main: {}  ·  Vision: {}",
                short(&st.models.main),
                short(&st.models.vision)
            ));
            let _ = metrics.set_text(format!(
                "{} tok · {:.0} tok/s · {} ms avg",
                human(st.metrics.total_tokens),
                st.metrics.tokens_per_sec,
                st.metrics.avg_response_ms
            ));
            let _ = synth.set_text(if st.enrichment.queue_depth > 0 {
                format!("🧠 Synthesizing — {} in queue", st.enrichment.queue_depth)
            } else {
                "🧠 Idle".to_string()
            });
            return;
        }
    }
    // Backend unreachable → clear the metrics + show stopped.
    let _ = status.set_text("○ LocalBook — backend stopped");
    let _ = models.set_text("Main: —");
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
