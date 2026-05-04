// ─── In-process Continuity Camera (macOS) ───────────────────────────────────
//
// This module replaces the old external `continuity-camera` Swift sidecar
// with an in-process implementation hosted inside LocalBook.app itself.
//
// WHY IN-PROCESS
// ──────────────
// Apple's Insert-from-iPhone flow (NSMenuItem.importFromDeviceIdentifier)
// routes captured image data back to the initiating Mac process via the
// pasteboard-services registry. That registry keys off Launch Services-
// registered `.app` bundles. A single-file adhoc-signed CLI sidecar is NOT
// registered with Launch Services, so even when AppKit correctly populates
// the menu with iPhone capture items, clicking one silently drops the data
// on the floor. LocalBook.app, by contrast, is a proper registered bundle —
// running the responder inside LocalBook makes routing unambiguous.
//
// FLOW
// ────
//   1. JS invokes `trigger_continuity_camera` (see lib.rs).
//   2. We hop to the main thread via `AppHandle::run_on_main_thread`.
//   3. We install a `ContinuityResponder` (NSResponder subclass conforming
//      to NSServicesMenuRequestor) into the main window's responder chain.
//      The responder sits between the window's contentView (typically the
//      Tauri WKWebView) and the window itself, so AppKit's responder-chain
//      walk from firstResponder reaches it.
//   4. We build an NSMenu containing a single placeholder NSMenuItem whose
//      identifier is `NSMenuItemImportFromDeviceIdentifier`. AppKit replaces
//      this placeholder at menu-open time with one item per iPhone capture
//      mode (Take Photo, Scan Documents, Add Sketch).
//   5. `popUpMenuPositioningItem` pops the menu at the centre of the window
//      and blocks the main thread until the menu closes. Return value tells
//      us whether the user picked an item or dismissed the menu.
//   6a. If dismissed: cleanup (restore responder chain), signal Err via the
//       oneshot, command returns.
//   6b. If picked: the iPhone runs its own capture UI. Eventually (seconds
//       to minutes later) AppKit sends `readSelection:` to our responder
//       with a pasteboard containing the captured image(s). We save to disk,
//       restore the responder chain, and signal Ok via the oneshot.
//   7. The tokio side of the command awaits the oneshot with a 3-minute
//       timeout and serialises the result back to JS.
//
// THREAD SAFETY
// ─────────────
// All AppKit manipulation happens on the main thread. The oneshot sender
// bridges to the tokio runtime — `oneshot::Sender::send` is synchronous and
// can be called from any thread, including the AppKit main thread.

#![cfg(target_os = "macos")]

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use objc2::rc::Retained;
use objc2::runtime::{AnyObject, NSObjectProtocol};
use objc2::{
    define_class, msg_send, ClassType, DefinedClass, MainThreadMarker, MainThreadOnly,
};
use objc2_app_kit::{
    NSApplication, NSBitmapImageFileType, NSBitmapImageRep, NSEvent, NSEventModifierFlags,
    NSEventType, NSImage, NSMenu, NSMenuItem, NSMenuItemImportFromDeviceIdentifier, NSPasteboard,
    NSResponder, NSUserInterfaceItemIdentification, NSUserInterfaceItemIdentifier, NSView,
    NSWindow,
};
use objc2_foundation::{NSArray, NSData, NSDictionary, NSPoint, NSRect, NSSize, NSString, NSURL};
use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime};
use tokio::sync::oneshot;

// ─── Diagnostic logger ───────────────────────────────────────────────────────
//
// Every `[continuity]` line goes both to stderr (visible in `npm run tauri
// dev` console) AND appended to ~/Library/Logs/LocalBook/continuity.log
// with a timestamp prefix, so the user can copy/paste it for bug reports
// even from a release build where stderr isn't visible.
//
// Path follows the macOS convention for user-level app logs (same place
// Console.app surfaces under "Log Reports").

fn continuity_log_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    PathBuf::from(home).join("Library/Logs/LocalBook/continuity.log")
}

fn log_line(s: &str) {
    eprintln!("{}", s);
    let path = continuity_log_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        use std::io::Write;
        // RFC-3339-ish timestamp without external chrono dep.
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        let _ = writeln!(f, "{:.3} {}", now, s);
    }
}

macro_rules! clog {
    ($($arg:tt)*) => { $crate::continuity::log_line(&format!($($arg)*)) };
}

#[derive(Clone, Serialize)]
pub struct ContinuityResult {
    pub status: String,
    pub paths: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

// ─── Responder ivars ─────────────────────────────────────────────────────────
//
// All fields are only touched on the main thread (AppKit callbacks) or via
// the main-thread install/finalize helpers. We use Mutex for interior
// mutability because `Retained<NSView>` is !Send but the mutex still gives
// us a clean borrow API; the lock is uncontested in practice.

pub struct ResponderState {
    /// Completion channel. `Some` until `finalize` takes it; `None` afterwards.
    sender: Option<oneshot::Sender<Result<Vec<String>, String>>>,
    /// The firstResponder that was active before we took over. Restored
    /// in `finalize` so the WKWebView regains keyboard focus.
    prev_first_responder: Option<Retained<NSResponder>>,
}

pub struct Ivars {
    output_dir: PathBuf,
    state: Mutex<ResponderState>,
    /// Flipped to true the first time AppKit calls validRequestor on us.
    /// If this stays false after the menu closes, AppKit never asked the
    /// responder chain whether we can accept image data — i.e. the
    /// import-from-iPhone substitution machinery never engaged.
    validate_called: AtomicBool,
}

// ─── ContinuityResponder — NSResponder + NSServicesMenuRequestor ─────────────

define_class!(
    //   v1.9.0 Sprint 9.2: subclass NSView instead of NSResponder, add
    //     ourselves as a 1×1 hidden subview of contentView, and call
    //     `[window makeFirstResponder:self]` so we're the very first hop
    //     AppKit walks. Switched to `popUpContextMenu:withEvent:forView:`.
    //     validRequestor WAS called, but only for text services — the
    //     Continuity Camera substitution never engaged because of the bug
    //     fixed in Sprint 9.3.
    //
    //   v1.9.0 Sprint 9.3 (this revision): the placeholder identifier was
    //     hardcoded as the literal string "NSMenuItemImportFromDeviceIdentifier"
    //     but the actual runtime value of that AppKit extern constant is an
    //     opaque string (NOT the symbol name). Now we import the real
    //     `NSMenuItemImportFromDeviceIdentifier` constant from objc2-app-kit.
    //     Also broadened `validRequestor` to return self for ANY non-nil
    //     returnType when sendType is nil (covers images, PDFs, sketches).
    #[unsafe(super(NSView))]
    #[name = "LBContinuityResponder"]
    #[ivars = Ivars]
    pub struct ContinuityResponder;

    unsafe impl NSObjectProtocol for ContinuityResponder {}

    impl ContinuityResponder {
        // Required for `[window makeFirstResponder:self]` to succeed;
        // NSView's default is NO.
        #[unsafe(method(acceptsFirstResponder))]
        fn accepts_first_responder(&self) -> bool { true }

        // AppKit walks the responder chain calling this when populating
        // the import-from-device menu. Returning `self` for an image return
        // type tells AppKit "this responder will receive the capture", and
        // AppKit then inserts iPhone capture items in place of the
        // NSMenuItemImportFromDeviceIdentifier placeholder.
        #[unsafe(method(validRequestorForSendType:returnType:))]
        fn valid_requestor(
            &self,
            send_type: *const NSString,
            return_type: *const NSString,
        ) -> *mut AnyObject {
            // Diagnostic: every chain-walk hit lands here. If menu still
            // shows greyed placeholder after this prints, AppKit got our
            // self-return but the iPhone isn't reachable; if it never
            // prints, AppKit isn't walking through us at all.
            let st_dbg = if send_type.is_null() { "nil".into() }
                         else { unsafe { (*send_type).to_string() } };
            let rt_dbg = if return_type.is_null() { "nil".into() }
                         else { unsafe { (*return_type).to_string() } };
            self.ivars().validate_called.store(true, Ordering::Relaxed);
            clog!("[continuity] validRequestor sendType={} returnType={}", st_dbg, rt_dbg);

            // Continuity Camera pattern: sendType is nil (we don't send
            // anything TO the iPhone), returnType is non-nil (we accept
            // the captured image/PDF back).  Return self for ANY non-nil
            // returnType when sendType is nil — this covers Take Photo
            // (image types), Scan Documents (PDF), and Add Sketch.
            if send_type.is_null() && !return_type.is_null() {
                clog!("[continuity]   → sendType=nil + returnType present → returning self");
                return self as *const _ as *mut AnyObject;
            }
            // For text services etc. (non-nil sendType), fall through to
            // super so the responder chain keeps walking normally.
            unsafe {
                msg_send![
                    super(self),
                    validRequestorForSendType: send_type,
                    returnType: return_type
                ]
            }
        }

        // NSServicesMenuRequestor — AppKit calls this with a pasteboard
        // containing the iPhone's captured image(s) once the iPhone-side
        // UI completes.
        #[unsafe(method(readSelectionFromPasteboard:))]
        fn read_selection(&self, pb: &NSPasteboard) -> bool {
            clog!("[continuity] readSelection fired");
            let paths = extract_images(pb, &self.ivars().output_dir);
            clog!("[continuity]   extracted {} path(s)", paths.len());
            let result = if paths.is_empty() {
                Err("no image data returned from iPhone".to_string())
            } else {
                Ok(paths)
            };
            self.finalize(result);
            true
        }

        // Protocol-required but unused — we never send selection data out.
        #[unsafe(method(writeSelectionToPasteboard:types:))]
        fn write_selection(
            &self,
            _pb: &NSPasteboard,
            _types: &NSArray<NSString>,
        ) -> bool {
            false
        }
    }
);

impl ContinuityResponder {
    fn new(
        mtm: MainThreadMarker,
        output_dir: PathBuf,
        sender: oneshot::Sender<Result<Vec<String>, String>>,
    ) -> Retained<Self> {
        let ivars = Ivars {
            output_dir,
            state: Mutex::new(ResponderState {
                sender: Some(sender),
                prev_first_responder: None,
            }),
            validate_called: AtomicBool::new(false),
        };
        let this = Self::alloc(mtm).set_ivars(ivars);
        // NSView is initialised with -initWithFrame: (not bare init).
        let frame = NSRect {
            origin: NSPoint { x: 0.0, y: 0.0 },
            size: NSSize { width: 1.0, height: 1.0 },
        };
        unsafe { msg_send![super(this), initWithFrame: frame] }
    }

    /// Send the result, restore firstResponder, and detach from the view
    /// hierarchy. Main-thread only. After this returns, `self` may be
    /// deallocated, so callers must not touch `self` afterwards.
    fn finalize(&self, result: Result<Vec<String>, String>) {
        let (sender, prev_first) = {
            let mut state = self.ivars().state.lock().unwrap();
            (state.sender.take(), state.prev_first_responder.take())
        };

        if let Some(prev) = prev_first {
            if let Some(window) = self.window() {
                let _ = window.makeFirstResponder(Some(&prev));
            }
        }

        if let Some(tx) = sender {
            let _ = tx.send(result);
        }

        // LAST. After this contentView's strong ref is dropped; if no other
        // strong ref remains, self deallocates immediately.
        self.removeFromSuperview();
    }
}

// ─── Pasteboard → disk ───────────────────────────────────────────────────────
//
// Continuity Camera delivers captured images as either:
//   (a) NSURL file references (common for Scan Documents — returns one
//       NSURL per page), or
//   (b) NSImage objects (common for Take Photo and Add Sketch).
// We try (a) first and fall back to (b). Both paths produce JPEG files in
// the output directory; the OCR pipeline accepts either.

fn extract_images(pb: &NSPasteboard, output_dir: &Path) -> Vec<String> {
    let mut paths = Vec::new();

    // --- Attempt 1: file URLs
    let url_class: &AnyObject = unsafe { &*(NSURL::class() as *const _ as *const AnyObject) };
    let url_classes: Retained<NSArray<AnyObject>> = NSArray::from_retained_slice(&[
        unsafe { Retained::retain(url_class as *const AnyObject as *mut AnyObject).unwrap() },
    ]);
    let url_objects: Option<Retained<NSArray<AnyObject>>> = unsafe {
        msg_send![
            pb,
            readObjectsForClasses: &*url_classes,
            options: std::ptr::null::<NSDictionary<NSString, AnyObject>>()
        ]
    };
    if let Some(arr) = url_objects {
        let count: usize = unsafe { msg_send![&*arr, count] };
        for i in 0..count {
            let obj: *mut AnyObject = unsafe { msg_send![&*arr, objectAtIndex: i] };
            if obj.is_null() {
                continue;
            }
            let url: &NSURL = unsafe { &*(obj as *const NSURL) };
            if let Some(path) = copy_file_url(url, output_dir) {
                paths.push(path);
            }
        }
    }
    if !paths.is_empty() {
        return paths;
    }

    // --- Attempt 2: NSImage objects
    let img_class: &AnyObject = unsafe { &*(NSImage::class() as *const _ as *const AnyObject) };
    let img_classes: Retained<NSArray<AnyObject>> = NSArray::from_retained_slice(&[
        unsafe { Retained::retain(img_class as *const AnyObject as *mut AnyObject).unwrap() },
    ]);
    let img_objects: Option<Retained<NSArray<AnyObject>>> = unsafe {
        msg_send![
            pb,
            readObjectsForClasses: &*img_classes,
            options: std::ptr::null::<NSDictionary<NSString, AnyObject>>()
        ]
    };
    if let Some(arr) = img_objects {
        let count: usize = unsafe { msg_send![&*arr, count] };
        for i in 0..count {
            let obj: *mut AnyObject = unsafe { msg_send![&*arr, objectAtIndex: i] };
            if obj.is_null() {
                continue;
            }
            let image: &NSImage = unsafe { &*(obj as *const NSImage) };
            if let Some(path) = save_image_as_jpeg(image, output_dir) {
                paths.push(path);
            }
        }
    }

    paths
}

/// Unique per-capture filename. We don't depend on `uuid`, so we synth from
/// wall-clock ns plus an atomic counter to avoid collisions within a single
/// multi-page Scan Documents result.
fn next_filename(output_dir: &Path, ext: &str) -> PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let ns = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let seq = COUNTER.fetch_add(1, Ordering::Relaxed);
    output_dir.join(format!("continuity_{}_{:04}.{}", ns, seq, ext))
}

fn copy_file_url(url: &NSURL, output_dir: &Path) -> Option<String> {
    let src_path_ns: Option<Retained<NSString>> = unsafe { msg_send![url, path] };
    let src_path = src_path_ns?.to_string();
    let ext = Path::new(&src_path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("jpg")
        .to_string();
    let dst = next_filename(output_dir, &ext);
    std::fs::copy(&src_path, &dst).ok()?;
    Some(dst.to_string_lossy().to_string())
}

fn save_image_as_jpeg(image: &NSImage, output_dir: &Path) -> Option<String> {
    // NSImage → TIFF bytes → NSBitmapImageRep → JPEG bytes → disk.
    let tiff: Option<Retained<NSData>> = unsafe { msg_send![image, TIFFRepresentation] };
    let tiff = tiff?;
    let bitmap: Option<Retained<NSBitmapImageRep>> = unsafe {
        msg_send![NSBitmapImageRep::class(), imageRepWithData: &*tiff]
    };
    let bitmap = bitmap?;
    let props: Retained<NSDictionary<NSString, AnyObject>> = NSDictionary::new();
    let jpeg_type: usize = NSBitmapImageFileType::JPEG.0 as usize;
    let jpeg: Option<Retained<NSData>> = unsafe {
        msg_send![
            &*bitmap,
            representationUsingType: jpeg_type,
            properties: &*props
        ]
    };
    let jpeg = jpeg?;
    let bytes_ptr: *const u8 = unsafe { msg_send![&*jpeg, bytes] };
    let length: usize = unsafe { msg_send![&*jpeg, length] };
    if bytes_ptr.is_null() || length == 0 {
        return None;
    }
    let slice = unsafe { std::slice::from_raw_parts(bytes_ptr, length) };
    let dst = next_filename(output_dir, "jpg");
    std::fs::write(&dst, slice).ok()?;
    Some(dst.to_string_lossy().to_string())
}

// ─── Public entry point ──────────────────────────────────────────────────────

pub async fn trigger<R: Runtime>(app: AppHandle<R>) -> Result<ContinuityResult, String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir: {e}"))?;
    let output_dir = data_dir.join("scans").join("continuity");
    std::fs::create_dir_all(&output_dir).map_err(|e| format!("mkdir: {e}"))?;

    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window not found".to_string())?;
    // Tauri returns a *mut c_void that is actually a *mut NSWindow. We pass
    // it to the main-thread closure as `usize` (Send) and cast back there.
    let ns_window_addr = window.ns_window().map_err(|e| format!("ns_window: {e}"))? as usize;

    let (tx, rx) = oneshot::channel::<Result<Vec<String>, String>>();

    let output_dir_clone = output_dir.clone();
    app.run_on_main_thread(move || {
        // SAFETY: run_on_main_thread contract guarantees main-thread execution.
        unsafe {
            let mtm = MainThreadMarker::new_unchecked();
            if ns_window_addr == 0 {
                let _ = tx.send(Err("null ns_window".into()));
                return;
            }
            let window_ptr = ns_window_addr as *mut NSWindow;
            let window: &NSWindow = &*window_ptr;
            install_and_pop(mtm, window, output_dir_clone, tx);
        }
    })
    .map_err(|e| format!("run_on_main_thread: {e}"))?;

    // 3 min budget is generous but bounded — covers slow iPhone auth / capture
    // without leaking a forever-pending command if the iPhone never responds.
    match tokio::time::timeout(std::time::Duration::from_secs(180), rx).await {
        Ok(Ok(Ok(paths))) => Ok(ContinuityResult {
            status: "ok".into(),
            paths,
            message: None,
        }),
        Ok(Ok(Err(msg))) => Ok(ContinuityResult {
            status: "error".into(),
            paths: vec![],
            message: Some(msg),
        }),
        Ok(Err(_)) => Ok(ContinuityResult {
            status: "error".into(),
            paths: vec![],
            message: Some("capture cancelled".into()),
        }),
        Err(_) => Ok(ContinuityResult {
            status: "error".into(),
            paths: vec![],
            message: Some(
                "capture timed out — no iPhone response within 3 minutes".into(),
            ),
        }),
    }
}

/// Must be called on the main thread. Installs the responder, pops the menu,
/// and either finalises immediately (user dismissed) or leaves the responder
/// in place to receive readSelection asynchronously.
unsafe fn install_and_pop(
    mtm: MainThreadMarker,
    window: &NSWindow,
    output_dir: PathBuf,
    tx: oneshot::Sender<Result<Vec<String>, String>>,
) {
    let content_view: Retained<NSView> = match window.contentView() {
        Some(v) => v,
        None => {
            let _ = tx.send(Err("window has no contentView".into()));
            return;
        }
    };
    clog!("[continuity] ── new session ── install_and_pop entered");
    clog!("[continuity] log file: {}", continuity_log_path().display());

    let responder = ContinuityResponder::new(mtm, output_dir, tx);

    // Sprint 9.5: cover the ENTIRE contentView so the synthesised right-
    // click hit-tests to our view (AppKit routes mouse events to the
    // topmost subview under the cursor).
    let bounds = content_view.bounds();
    responder.setFrame(bounds);
    // Transparent so the user doesn't see it, but NOT hidden (hidden
    // views don't receive mouse events).
    let _: () = unsafe { msg_send![&*responder, setAlphaValue: 0.01_f64] };
    content_view.addSubview(&responder);
    clog!(
        "[continuity] overlay subview added ({}×{})",
        bounds.size.width, bounds.size.height
    );

    // Make our view the firstResponder so AppKit's responder-chain walk
    // for menu validation starts here.
    let prev_first: Option<Retained<NSResponder>> = window.firstResponder();
    let became_first: bool = window.makeFirstResponder(Some(&responder));
    clog!(
        "[continuity] makeFirstResponder result={} (had_prev={})",
        became_first, prev_first.is_some()
    );
    {
        let mut state = responder.ivars().state.lock().unwrap();
        state.prev_first_responder = prev_first;
    }

    // Build the menu with the magic placeholder. Set it as the view's
    // context menu so that the default `menuForEvent:` returns it when
    // AppKit processes the right-click.
    let menu: Retained<NSMenu> = NSMenu::new(mtm);
    let placeholder: Retained<NSMenuItem> = NSMenuItem::new(mtm);
    let title = NSString::from_str("Import from Device");
    placeholder.setTitle(&title);
    let ident: &NSUserInterfaceItemIdentifier = NSMenuItemImportFromDeviceIdentifier;
    clog!("[continuity] placeholder identifier = {:?}", ident.to_string());
    placeholder.setIdentifier(Some(ident));
    menu.addItem(&placeholder);
    let _: () = unsafe { msg_send![&*responder, setMenu: &*menu] };
    clog!("[continuity] menu set on responder view");

    // Sprint 9.5 — THE KEY FIX: send a real RightMouseDown through
    // [NSApp sendEvent:] so AppKit runs the full context-menu path:
    //
    //   NSApp sendEvent:  →  NSWindow sendEvent:
    //     →  hit-test finds our overlay view
    //     →  [view rightMouseDown:]
    //     →  [view menuForEvent:]  (returns our menu with placeholder)
    //     →  AppKit performs importFromDeviceIdentifier substitution
    //     →  [NSMenu popUpContextMenu:withEvent:forView:] (blocks)
    //
    // This is the exact same code path TextEdit / Notes use for their
    // right-click "Insert from iPhone" submenu. Our previous approach
    // called popUpContextMenu directly, which SKIPPED the substitution.
    let cx = bounds.origin.x + bounds.size.width / 2.0;
    let cy = bounds.origin.y + bounds.size.height / 2.0;
    let window_number: isize = window.windowNumber();
    clog!(
        "[continuity] synthesising RightMouseDown at window-coord ({}, {}), windowNumber={}",
        cx, cy, window_number
    );
    let event_opt: Option<Retained<NSEvent>> =
        NSEvent::mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure(
            NSEventType::RightMouseDown,
            NSPoint { x: cx, y: cy },
            NSEventModifierFlags::empty(),
            0.0,
            window_number,
            None,
            0,
            1,
            1.0,
        );
    let event = match event_opt {
        Some(e) => e,
        None => {
            clog!("[continuity] FAILED to synthesise NSEvent");
            responder.finalize(Err("failed to synthesise RightMouseDown event".into()));
            return;
        }
    };

    let app = NSApplication::sharedApplication(mtm);
    clog!("[continuity] sending RightMouseDown via [NSApp sendEvent:]");
    app.sendEvent(&event);
    clog!("[continuity] sendEvent returned");

    // After the context menu closes, dump menu state for diagnostics.
    let validated = responder.ivars().validate_called.load(Ordering::Relaxed);
    clog!(
        "[continuity] validRequestor was called: {}",
        validated
    );
    let item_count: isize = unsafe { msg_send![&*menu, numberOfItems] };
    clog!("[continuity] menu now has {} item(s):", item_count);
    for i in 0..item_count {
        let item: *mut AnyObject = unsafe { msg_send![&*menu, itemAtIndex: i] };
        if !item.is_null() {
            let t: Option<Retained<NSString>> = unsafe { msg_send![item, title] };
            let enabled: bool = unsafe { msg_send![item, isEnabled] };
            let has_sub: bool = unsafe { msg_send![item, hasSubmenu] };
            clog!(
                "[continuity]   [{}] title={:?} enabled={} hasSubmenu={}",
                i,
                t.as_ref().map(|s| s.to_string()),
                enabled,
                has_sub,
            );
            if has_sub {
                let sub: *mut AnyObject = unsafe { msg_send![item, submenu] };
                if !sub.is_null() {
                    let sub_count: isize = unsafe { msg_send![sub, numberOfItems] };
                    for j in 0..sub_count {
                        let si: *mut AnyObject = unsafe { msg_send![sub, itemAtIndex: j] };
                        if !si.is_null() {
                            let st: Option<Retained<NSString>> = unsafe { msg_send![si, title] };
                            let se: bool = unsafe { msg_send![si, isEnabled] };
                            clog!(
                                "[continuity]     [{}.{}] title={:?} enabled={}",
                                i, j,
                                st.as_ref().map(|s| s.to_string()),
                                se,
                            );
                        }
                    }
                }
            }
        }
    }

    if !validated {
        responder.finalize(Err(
            "AppKit did not call validRequestor — device substitution did not engage".to_string(),
        ));
        return;
    }
    clog!("[continuity] menu closed; awaiting readSelection or timeout");
}
