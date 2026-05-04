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
use std::sync::Mutex;

use objc2::rc::Retained;
use objc2::runtime::{AnyObject, NSObjectProtocol};
use objc2::{
    define_class, msg_send, ClassType, DefinedClass, MainThreadMarker, MainThreadOnly,
};
use objc2_app_kit::{
    NSBitmapImageFileType, NSBitmapImageRep, NSImage, NSMenu, NSMenuItem, NSPasteboard,
    NSResponder, NSUserInterfaceItemIdentification, NSUserInterfaceItemIdentifier, NSView,
    NSWindow,
};
use objc2_foundation::{NSArray, NSData, NSDictionary, NSPoint, NSString, NSURL};
use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime};
use tokio::sync::oneshot;

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
    /// The responder that was content_view.nextResponder BEFORE we inserted
    /// ourselves. Restored on finalize so we don't leak a dangling pointer.
    prev_next: Option<Retained<NSResponder>>,
    /// The view we patched. Held strongly so it outlives the iPhone capture.
    content_view: Option<Retained<NSView>>,
}

pub struct Ivars {
    output_dir: PathBuf,
    state: Mutex<ResponderState>,
}

// ─── ContinuityResponder — NSResponder + NSServicesMenuRequestor ─────────────

define_class!(
    #[unsafe(super(NSResponder))]
    #[name = "LBContinuityResponder"]
    #[ivars = Ivars]
    pub struct ContinuityResponder;

    unsafe impl NSObjectProtocol for ContinuityResponder {}

    impl ContinuityResponder {
        // AppKit walks the responder chain calling this when populating
        // the import-from-device menu. Returning `self` for an image return
        // type tells AppKit "this responder will receive the capture", and
        // AppKit then inserts iPhone capture items in place of the
        // NSMenuItemImportFromDeviceIdentifier placeholder.
        #[unsafe(method(validRequestorForSendType:returnType:))]
        fn valid_requestor(
            &self,
            _send_type: *const NSString,
            return_type: *const NSString,
        ) -> *mut AnyObject {
            if !return_type.is_null() {
                // Check whether the requested return type is an image type.
                let image_types: Retained<NSArray<NSString>> = NSImage::imageTypes();
                let rt: &NSString = unsafe { &*return_type };
                let contains: bool =
                    unsafe { msg_send![&*image_types, containsObject: rt] };
                if contains {
                    return self as *const _ as *mut AnyObject;
                }
            }
            // Fall through to super for everything else.
            unsafe {
                msg_send![
                    super(self),
                    validRequestorForSendType: _send_type,
                    returnType: return_type
                ]
            }
        }

        // NSServicesMenuRequestor — AppKit calls this with a pasteboard
        // containing the iPhone's captured image(s) once the iPhone-side
        // UI completes.
        #[unsafe(method(readSelectionFromPasteboard:))]
        fn read_selection(&self, pb: &NSPasteboard) -> bool {
            let paths = extract_images(pb, &self.ivars().output_dir);
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
        _mtm: MainThreadMarker,
        output_dir: PathBuf,
        sender: oneshot::Sender<Result<Vec<String>, String>>,
    ) -> Retained<Self> {
        let ivars = Ivars {
            output_dir,
            state: Mutex::new(ResponderState {
                sender: Some(sender),
                prev_next: None,
                content_view: None,
            }),
        };
        let this = Self::alloc(_mtm).set_ivars(ivars);
        unsafe { msg_send![super(this), init] }
    }

    /// Send the result over the oneshot (if not already sent) and restore
    /// the responder chain. Safe to call from the main thread only.
    fn finalize(&self, result: Result<Vec<String>, String>) {
        let mut state = self.ivars().state.lock().unwrap();
        // Restore previous nextResponder on content view.
        if let Some(cv) = state.content_view.take() {
            unsafe {
                let prev: Option<&NSResponder> = state.prev_next.as_deref();
                cv.setNextResponder(prev);
            }
        }
        if let Some(tx) = state.sender.take() {
            let _ = tx.send(result);
        }
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

    let responder = ContinuityResponder::new(mtm, output_dir, tx);

    // Patch the responder chain: contentView → us → (original nextResponder).
    // Save the original so finalize() can restore it.
    let prev_next: Option<Retained<NSResponder>> = content_view.nextResponder();
    responder.setNextResponder(prev_next.as_deref());
    {
        let super_responder: &NSResponder = &*responder;
        content_view.setNextResponder(Some(super_responder));
    }
    {
        let mut state = responder.ivars().state.lock().unwrap();
        state.content_view = Some(content_view.clone());
        state.prev_next = prev_next;
    }

    // Build the menu. The single placeholder item is replaced by AppKit
    // with per-capture-mode items when the menu opens.
    let menu: Retained<NSMenu> = NSMenu::new(mtm);
    let placeholder: Retained<NSMenuItem> = NSMenuItem::new(mtm);
    let title = NSString::from_str("Insert from iPhone");
    placeholder.setTitle(&title);
    // NSUserInterfaceItemIdentifier is a type alias for NSString.
    let ident: Retained<NSUserInterfaceItemIdentifier> =
        NSString::from_str("NSMenuItemImportFromDeviceIdentifier");
    placeholder.setIdentifier(Some(&ident));
    menu.addItem(&placeholder);

    // Anchor the menu at the centre of the main window's content view.
    let bounds = content_view.bounds();
    let point = NSPoint {
        x: bounds.origin.x + bounds.size.width / 2.0,
        y: bounds.origin.y + bounds.size.height / 2.0,
    };
    // Returns true iff the user picked an item (vs. dismissed the menu).
    // popUpMenuPositioningItem_atLocation_inView blocks until menu close.
    let picked: bool = menu.popUpMenuPositioningItem_atLocation_inView(
        None,
        point,
        Some(&content_view),
    );

    if !picked {
        // Cancelled — finalize now (also restores the responder chain).
        responder.finalize(Err("user cancelled".to_string()));
    }
    // If picked, readSelection will fire asynchronously on `responder` when
    // the iPhone returns image data. `Retained<ContinuityResponder>` dropping
    // at function end is fine: the responder chain (window → contentView →
    // responder) holds strong references via nextResponder retention, so the
    // object stays alive until finalize() detaches it.
}
