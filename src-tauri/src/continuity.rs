// In-process Continuity Camera capture via AVCaptureDevice.
//
// REVISION HISTORY
//
//   v1.9.0 Sprints 9.1–9.8 (DEAD-END): tried to make
//     NSMenuItemImportFromDeviceIdentifier work programmatically. Apple's
//     menu-substitution machinery only fires from the menu-bar tracking
//     loop (user physically clicks a menu bar item). Every native macOS
//     app (TextEdit, Notes, Pages, Keynote, Finder) accepts that
//     constraint. No popUpContextMenu, sendEvent, or popUpMenuPositioning-
//     Item path triggers the substitution.
//
//   v1.9.0 Sprint 9.9 (this revision): pivot to AVCaptureDevice.
//     Continuity Camera also exposes the iPhone as a regular external
//     camera (since macOS Ventura 13). We enumerate AVCaptureDevices of
//     type AVCaptureDeviceTypeContinuityCamera, build an AVCaptureSession
//     with an AVCapturePhotoOutput, show our own native NSWindow with a
//     live preview layer, and a Capture / Cancel button pair on the Mac
//     side. This is the same mechanism browser WebRTC code uses (the
//     iPhone is just a webcam from the OS's perspective). No menu bar
//     hacks, no responder chain, no NSServicesMenuRequestor — just a
//     plain capture session we drive ourselves. Required Info.plist key
//     `NSCameraUseContinuityCameraDeviceType` is already present.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use objc2::rc::Retained;
use objc2::runtime::{AnyObject, NSObject, NSObjectProtocol, ProtocolObject, Sel};
use objc2::{
    define_class, msg_send, sel, AllocAnyThread, DefinedClass, MainThreadMarker, MainThreadOnly,
};

use objc2_app_kit::{
    NSBackingStoreType, NSBezelStyle, NSButton, NSColor, NSScreen, NSTextAlignment, NSTextField,
    NSView, NSWindow, NSWindowStyleMask,
};
use objc2_av_foundation::{
    AVCaptureDevice, AVCaptureDeviceDiscoverySession, AVCaptureDeviceInput,
    AVCaptureDeviceType, AVCaptureDeviceTypeContinuityCamera, AVCapturePhoto,
    AVCapturePhotoCaptureDelegate, AVCapturePhotoOutput, AVCapturePhotoSettings, AVCaptureSession,
    AVCaptureSessionPresetPhoto, AVCaptureVideoPreviewLayer, AVMediaTypeVideo,
};
use objc2_foundation::{NSArray, NSData, NSError, NSPoint, NSRect, NSSize, NSString};
use objc2_quartz_core::CALayer;

use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime};
use tokio::sync::oneshot;

// ─── Diagnostic logger ───────────────────────────────────────────────────────
//
// Every `[continuity]` line goes both to stderr AND appended to
// ~/Library/Logs/LocalBook/continuity.log so multi-step issues stay
// post-mortem-able.

fn continuity_log_path() -> PathBuf {
    if let Some(home) = dirs_home() {
        let dir = home.join("Library").join("Logs").join("LocalBook");
        let _ = std::fs::create_dir_all(&dir);
        dir.join("continuity.log")
    } else {
        PathBuf::from("/tmp/localbook-continuity.log")
    }
}

fn dirs_home() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

fn log_line(line: &str) {
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| format!("{}.{:03}", d.as_secs(), d.subsec_millis()))
        .unwrap_or_else(|_| "?".to_string());
    let stamped = format!("{ts} {line}\n");
    eprint!("{stamped}");
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(continuity_log_path())
    {
        use std::io::Write;
        let _ = f.write_all(stamped.as_bytes());
    }
}

macro_rules! clog {
    ($($arg:tt)*) => {{
        log_line(&format!($($arg)*));
    }};
}

// ─── Result type returned to JS via Tauri command ────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct ContinuityResult {
    pub status: String,
    pub paths: Vec<String>,
    pub message: Option<String>,
}

// ─── CapturePhotoDelegate — receives the JPEG bytes after capturePhoto: ──────

pub struct DelegateIvars {
    output_dir: PathBuf,
    controller: Mutex<Option<Retained<CaptureController>>>,
}

define_class!(
    #[unsafe(super(NSObject))]
    #[name = "LBCapturePhotoDelegate"]
    #[ivars = DelegateIvars]
    pub struct CapturePhotoDelegate;

    unsafe impl NSObjectProtocol for CapturePhotoDelegate {}

    unsafe impl AVCapturePhotoCaptureDelegate for CapturePhotoDelegate {
        // Called on a "common" (non-main) dispatch queue once the photo is
        // ready. We grab the JPEG bytes, write to disk, and tell the
        // controller (on the main thread) to finalize.
        #[unsafe(method(captureOutput:didFinishProcessingPhoto:error:))]
        unsafe fn did_finish_processing_photo(
            &self,
            _output: &AVCapturePhotoOutput,
            photo: &AVCapturePhoto,
            error: Option<&NSError>,
        ) {
            if let Some(err) = error {
                let msg = err.localizedDescription().to_string();
                clog!("[continuity] capture error: {msg}");
                self.report_failure(format!("capture error: {msg}"));
                return;
            }

            let data: Option<Retained<NSData>> = unsafe { photo.fileDataRepresentation() };
            let Some(data) = data else {
                clog!("[continuity] photo has no fileDataRepresentation");
                self.report_failure("photo has no file representation".into());
                return;
            };

            let dir = self.ivars().output_dir.clone();
            let path = next_filename(&dir, "jpg");
            match write_nsdata_to_disk(&data, &path) {
                Ok(()) => {
                    clog!("[continuity] saved {} bytes to {}", data.length(), path.display());
                    let path_str = path.to_string_lossy().to_string();
                    self.report_success(vec![path_str]);
                }
                Err(e) => {
                    clog!("[continuity] write failed: {e}");
                    self.report_failure(format!("failed to write capture: {e}"));
                }
            }
        }
    }
);

impl CapturePhotoDelegate {
    fn new(mtm: MainThreadMarker, output_dir: PathBuf) -> Retained<Self> {
        let _ = mtm; // delegate itself is not main-thread-only
        let ivars = DelegateIvars {
            output_dir,
            controller: Mutex::new(None),
        };
        let this = Self::alloc().set_ivars(ivars);
        unsafe { msg_send![super(this), init] }
    }

    fn set_controller(&self, controller: Retained<CaptureController>) {
        *self.ivars().controller.lock().unwrap() = Some(controller);
    }

    fn report_success(&self, paths: Vec<String>) {
        if let Some(ctrl) = self.ivars().controller.lock().unwrap().as_ref() {
            ctrl.dispatch_finalize(Ok(paths));
        }
    }

    fn report_failure(&self, msg: String) {
        if let Some(ctrl) = self.ivars().controller.lock().unwrap().as_ref() {
            ctrl.dispatch_finalize(Err(msg));
        }
    }
}

// ─── CaptureController — owns NSWindow, session, button targets ──────────────

pub struct ControllerIvars {
    window: Mutex<Option<Retained<NSWindow>>>,
    session: Mutex<Option<Retained<AVCaptureSession>>>,
    photo_output: Mutex<Option<Retained<AVCapturePhotoOutput>>>,
    delegate: Mutex<Option<Retained<CapturePhotoDelegate>>>,
    sender: Mutex<Option<oneshot::Sender<Result<Vec<String>, String>>>>,
    finalized: AtomicBool,
    // Holds the pending result while we hop from a background queue back
    // onto the main thread via `performSelectorOnMainThread:`.
    pending_result: Mutex<Option<Result<Vec<String>, String>>>,
}

define_class!(
    #[unsafe(super(NSObject))]
    #[name = "LBCaptureController"]
    #[ivars = ControllerIvars]
    #[thread_kind = MainThreadOnly]
    pub struct CaptureController;

    unsafe impl NSObjectProtocol for CaptureController {}

    impl CaptureController {
        // Capture button action (main thread).
        #[unsafe(method(capturePressed:))]
        fn capture_pressed(&self, _sender: *mut AnyObject) {
            clog!("[continuity] Capture button pressed");
            self.do_capture();
        }

        // Cancel button action (main thread).
        #[unsafe(method(cancelPressed:))]
        fn cancel_pressed(&self, _sender: *mut AnyObject) {
            clog!("[continuity] Cancel button pressed");
            self.finalize(Err("capture cancelled".into()));
        }

        // Hop-target for `performSelectorOnMainThread:`. Called by the
        // photo-capture delegate from a background queue; we run the
        // actual finalize on the main thread.
        #[unsafe(method(applyPendingResult))]
        fn apply_pending_result(&self) {
            let pending = self.ivars().pending_result.lock().unwrap().take();
            if let Some(result) = pending {
                self.finalize(result);
            }
        }
    }
);

impl CaptureController {
    fn new(mtm: MainThreadMarker, sender: oneshot::Sender<Result<Vec<String>, String>>) -> Retained<Self> {
        let ivars = ControllerIvars {
            window: Mutex::new(None),
            session: Mutex::new(None),
            photo_output: Mutex::new(None),
            delegate: Mutex::new(None),
            sender: Mutex::new(Some(sender)),
            finalized: AtomicBool::new(false),
            pending_result: Mutex::new(None),
        };
        let this = Self::alloc(mtm).set_ivars(ivars);
        unsafe { msg_send![super(this), init] }
    }

    /// Trigger the actual photo capture. Main thread.
    fn do_capture(&self) {
        let photo_output = self.ivars().photo_output.lock().unwrap().clone();
        let delegate = self.ivars().delegate.lock().unwrap().clone();
        let (Some(output), Some(delegate)) = (photo_output, delegate) else {
            clog!("[continuity] do_capture: missing output or delegate");
            self.finalize(Err("internal: missing capture pipeline".into()));
            return;
        };

        // Default settings = JPEG / HEIF baseline.
        let settings: Retained<AVCapturePhotoSettings> =
            unsafe { AVCapturePhotoSettings::photoSettings() };
        let proto: &ProtocolObject<dyn AVCapturePhotoCaptureDelegate> =
            ProtocolObject::from_ref(&*delegate);
        clog!("[continuity] dispatching capturePhotoWithSettings:delegate:");
        unsafe { output.capturePhotoWithSettings_delegate(&settings, proto) };
    }

    /// Called from any thread; if we're not on main, marshal via
    /// `performSelectorOnMainThread:`.
    fn dispatch_finalize(&self, result: Result<Vec<String>, String>) {
        // Stash the result so the main-thread method can pick it up.
        *self.ivars().pending_result.lock().unwrap() = Some(result);
        let sel = sel!(applyPendingResult);
        unsafe {
            let _: () = msg_send![
                self,
                performSelectorOnMainThread: sel,
                withObject: std::ptr::null::<AnyObject>(),
                waitUntilDone: false
            ];
        }
    }

    /// Tear down the session, close the window, send the result. Main thread.
    fn finalize(&self, result: Result<Vec<String>, String>) {
        if self.ivars().finalized.swap(true, Ordering::SeqCst) {
            // Already finalized; ignore double-cancel etc.
            return;
        }
        clog!(
            "[continuity] finalize: {}",
            match &result {
                Ok(p) => format!("ok ({} path(s))", p.len()),
                Err(e) => format!("err: {e}"),
            }
        );

        if let Some(session) = self.ivars().session.lock().unwrap().take() {
            unsafe { session.stopRunning() };
        }
        if let Some(window) = self.ivars().window.lock().unwrap().take() {
            window.orderOut(None);
            window.close();
        }

        let sender = self.ivars().sender.lock().unwrap().take();
        if let Some(tx) = sender {
            let _ = tx.send(result);
        }
    }
}

// ─── Public entry point ──────────────────────────────────────────────────────

pub async fn trigger<R: Runtime>(app: AppHandle<R>) -> Result<ContinuityResult, String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir: {e}"))?;
    let output_dir = data_dir.join("scans").join("continuity");
    std::fs::create_dir_all(&output_dir).map_err(|e| format!("mkdir: {e}"))?;

    let (tx, rx) = oneshot::channel::<Result<Vec<String>, String>>();
    let output_dir_cloned = output_dir.clone();

    app.run_on_main_thread(move || {
        // SAFETY: run_on_main_thread guarantees main-thread execution.
        unsafe {
            let mtm = MainThreadMarker::new_unchecked();
            install_capture_window(mtm, output_dir_cloned, tx);
        }
    })
    .map_err(|e| format!("run_on_main_thread: {e}"))?;

    // 3-min budget — covers slow iPhone wake-up + auth + framing.
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
            message: Some("capture timed out — no iPhone response within 3 minutes".into()),
        }),
    }
}

// ─── Setup: discover device, build session, show window ──────────────────────

unsafe fn install_capture_window(
    mtm: MainThreadMarker,
    output_dir: PathBuf,
    tx: oneshot::Sender<Result<Vec<String>, String>>,
) {
    clog!("[continuity] ── new session ── install_capture_window entered");
    clog!("[continuity] log file: {}", continuity_log_path().display());

    let controller = CaptureController::new(mtm, tx);

    // 1. Discover Continuity Camera devices.
    let continuity_type: &AVCaptureDeviceType = unsafe { AVCaptureDeviceTypeContinuityCamera };
    let device_types: Retained<NSArray<AVCaptureDeviceType>> =
        NSArray::from_slice(&[continuity_type]);
    let media_type: Option<&NSString> = unsafe { AVMediaTypeVideo };
    let session_d: Retained<AVCaptureDeviceDiscoverySession> = unsafe {
        AVCaptureDeviceDiscoverySession::discoverySessionWithDeviceTypes_mediaType_position(
            &device_types,
            media_type,
            objc2_av_foundation::AVCaptureDevicePosition::Unspecified,
        )
    };
    let devices: Retained<NSArray<AVCaptureDevice>> = unsafe { session_d.devices() };
    let count = devices.count();
    clog!("[continuity] found {count} Continuity Camera device(s)");

    if count == 0 {
        controller.finalize(Err(
            "no iPhone or iPad found — make sure your device is unlocked, on the same Apple ID, \
             and Wi-Fi + Bluetooth are on for both"
                .into(),
        ));
        return;
    }

    let device: Retained<AVCaptureDevice> = devices.objectAtIndex(0);
    let device_name: String = unsafe { device.localizedName() }.to_string();
    clog!("[continuity] using device: {device_name}");

    // 2. Build AVCaptureSession with input + photo output.
    let session: Retained<AVCaptureSession> = unsafe { AVCaptureSession::new() };
    if unsafe { session.canSetSessionPreset(AVCaptureSessionPresetPhoto) } {
        unsafe { session.setSessionPreset(AVCaptureSessionPresetPhoto) };
    }

    let input: Retained<AVCaptureDeviceInput> = match unsafe {
        AVCaptureDeviceInput::initWithDevice_error(AVCaptureDeviceInput::alloc(), &device)
    } {
        Ok(i) => i,
        Err(e) => {
            let msg = unsafe { e.localizedDescription() }.to_string();
            clog!("[continuity] AVCaptureDeviceInput error: {msg}");
            controller.finalize(Err(format!("camera input error: {msg}")));
            return;
        }
    };
    if unsafe { session.canAddInput(&input) } {
        unsafe { session.addInput(&input) };
    } else {
        controller.finalize(Err("session refused camera input".into()));
        return;
    }

    let photo_output: Retained<AVCapturePhotoOutput> = unsafe { AVCapturePhotoOutput::new() };
    if unsafe { session.canAddOutput(&photo_output) } {
        unsafe { session.addOutput(&photo_output) };
    } else {
        controller.finalize(Err("session refused photo output".into()));
        return;
    }

    // 3. Photo capture delegate.
    let delegate = CapturePhotoDelegate::new(mtm, output_dir.clone());
    delegate.set_controller(controller.clone());

    *controller.ivars().session.lock().unwrap() = Some(session.clone());
    *controller.ivars().photo_output.lock().unwrap() = Some(photo_output.clone());
    *controller.ivars().delegate.lock().unwrap() = Some(delegate.clone());

    // 4. Build the NSWindow with a layer-backed content view.
    let win_w: f64 = 720.0;
    let win_h: f64 = 540.0;

    // Centre on screen.
    let screen = NSScreen::mainScreen(mtm);
    let screen_frame = screen
        .as_deref()
        .map(|s| s.frame())
        .unwrap_or(NSRect {
            origin: NSPoint { x: 0.0, y: 0.0 },
            size: NSSize {
                width: 1440.0,
                height: 900.0,
            },
        });
    let win_origin_x = screen_frame.origin.x + (screen_frame.size.width - win_w) / 2.0;
    let win_origin_y = screen_frame.origin.y + (screen_frame.size.height - win_h) / 2.0;
    let frame = NSRect {
        origin: NSPoint {
            x: win_origin_x,
            y: win_origin_y,
        },
        size: NSSize {
            width: win_w,
            height: win_h,
        },
    };

    let style = NSWindowStyleMask::Titled
        | NSWindowStyleMask::Closable
        | NSWindowStyleMask::Resizable;
    let window: Retained<NSWindow> = {
        let alloc = NSWindow::alloc(mtm);
        unsafe {
            NSWindow::initWithContentRect_styleMask_backing_defer(
                alloc,
                frame,
                style,
                NSBackingStoreType::Buffered,
                false,
            )
        }
    };
    let title = NSString::from_str(&format!("Capture from {device_name}"));
    window.setTitle(&title);
    window.setReleasedWhenClosed(false);

    let content_view: Retained<NSView> = window.contentView().expect("new window has contentView");
    content_view.setWantsLayer(true);
    let bg_layer: Retained<CALayer> = unsafe { CALayer::new() };
    let black: Retained<NSColor> = NSColor::blackColor();
    let bg_cg = unsafe { black.CGColor() };
    unsafe { bg_layer.setBackgroundColor(Some(&bg_cg)) };
    content_view.setLayer(Some(&bg_layer));

    // 5. Live preview layer = top portion of the window.
    let preview_h = win_h - 80.0;
    let preview_frame = NSRect {
        origin: NSPoint { x: 0.0, y: 80.0 },
        size: NSSize {
            width: win_w,
            height: preview_h,
        },
    };
    let preview_layer: Retained<AVCaptureVideoPreviewLayer> = unsafe {
        let alloc = AVCaptureVideoPreviewLayer::alloc();
        AVCaptureVideoPreviewLayer::initWithSession(alloc, &session)
    };
    unsafe {
        preview_layer.setFrame(preview_frame);
        // Resize-aspect = full preview, letterboxed.
        let mode = NSString::from_str("AVLayerVideoGravityResizeAspect");
        preview_layer.setVideoGravity(&mode);
        bg_layer.addSublayer(&preview_layer);
    }

    // 6. Capture + Cancel buttons (main thread; targeted at controller).
    let btn_w: f64 = 140.0;
    let btn_h: f64 = 36.0;
    let btn_y: f64 = 20.0;
    let cancel_x = (win_w / 2.0) - btn_w - 10.0;
    let capture_x = (win_w / 2.0) + 10.0;

    let cancel_btn = make_button(
        mtm,
        "Cancel",
        NSRect {
            origin: NSPoint {
                x: cancel_x,
                y: btn_y,
            },
            size: NSSize {
                width: btn_w,
                height: btn_h,
            },
        },
        &controller,
        sel!(cancelPressed:),
    );
    let capture_btn = make_button(
        mtm,
        "Capture",
        NSRect {
            origin: NSPoint {
                x: capture_x,
                y: btn_y,
            },
            size: NSSize {
                width: btn_w,
                height: btn_h,
            },
        },
        &controller,
        sel!(capturePressed:),
    );
    unsafe {
        capture_btn.setKeyEquivalent(&NSString::from_str("\r"));
    }

    content_view.addSubview(&cancel_btn);
    content_view.addSubview(&capture_btn);

    // Optional helper text under the preview.
    let label_y = btn_y + btn_h + 8.0;
    let label_frame = NSRect {
        origin: NSPoint { x: 16.0, y: label_y },
        size: NSSize {
            width: win_w - 32.0,
            height: 20.0,
        },
    };
    let label: Retained<NSTextField> = unsafe {
        let f: Retained<NSTextField> = msg_send![NSTextField::alloc(mtm), init];
        f.setFrame(label_frame);
        f.setStringValue(&NSString::from_str(
            "Frame the page on your iPhone, then click Capture.",
        ));
        f.setEditable(false);
        f.setBezeled(false);
        f.setDrawsBackground(false);
        f.setAlignment(NSTextAlignment::Center);
        f.setTextColor(Some(&NSColor::secondaryLabelColor()));
        f
    };
    content_view.addSubview(&label);

    *controller.ivars().window.lock().unwrap() = Some(window.clone());

    // 7. Show the window.
    window.makeKeyAndOrderFront(None);
    clog!("[continuity] window shown ({}×{})", win_w, win_h);

    // 8. Start the capture session — this can briefly block, but only
    //    a few ms in practice on already-paired Continuity devices.
    unsafe { session.startRunning() };
    clog!("[continuity] AVCaptureSession startRunning returned");
}

// ─── Small helpers ───────────────────────────────────────────────────────────

fn make_button(
    mtm: MainThreadMarker,
    title: &str,
    frame: NSRect,
    target: &CaptureController,
    action: Sel,
) -> Retained<NSButton> {
    let b: Retained<NSButton> = unsafe { msg_send![NSButton::alloc(mtm), init] };
    unsafe {
        b.setFrame(frame);
        b.setTitle(&NSString::from_str(title));
        b.setBezelStyle(NSBezelStyle::Rounded);
        b.setTarget(Some(target));
        b.setAction(Some(action));
    }
    b
}

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

fn write_nsdata_to_disk(data: &NSData, path: &Path) -> Result<(), String> {
    if data.length() == 0 {
        return Err("empty NSData".into());
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let path_str = NSString::from_str(&path.to_string_lossy());
    let ok = data.writeToFile_atomically(&path_str, true);
    if ok {
        Ok(())
    } else {
        Err(format!("NSData writeToFile failed for {}", path.display()))
    }
}
