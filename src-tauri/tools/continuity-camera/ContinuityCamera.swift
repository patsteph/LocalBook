// ContinuityCamera.swift
//
// CLI helper that brings iPhone-side capture controls into LocalBook via
// Apple's documented "Insert from iPhone or iPad" mechanism (a.k.a.
// NSMenuItem.importFromDeviceIdentifier). The user clicks a single
// "Capture from iPhone" button on the Mac, and from that point onward the
// iPhone owns the entire capture experience: tap Take Photo, Scan
// Documents, or Add Sketch directly on the iPhone screen, and the
// resulting image(s) flow back to this sidecar via the system pasteboard.
//
// Two output modes:
//
//   continuity-camera --list
//       Enumerate every camera AVFoundation can see and emit JSON. Used
//       by the frontend for diagnostics and for distinguishing "iPhone
//       paired" from "iPhone not detected" before launching capture.
//       No window. Uses AVFoundation only — does NOT touch the import-
//       from-device API.
//
//   continuity-camera <output_dir> [--camera <id>] [--include-non-continuity]
//       Capture mode. Shows a small launcher window with one button,
//       which pops up a contextual menu that AppKit auto-fills with
//       per-device entries like "Take Photo (My iPhone)" and "Scan
//       Documents (My iPhone)". The legacy --camera and
//       --include-non-continuity flags are accepted but ignored — AppKit
//       picks the device from the iPhone's side, and "Scan Documents"
//       handles multi-page batches natively (all pages return in one
//       pasteboard payload, no Mac round-trip per page).
//
// Output (stdout, single JSON line, in both success and failure cases):
//
//     {"status":"ok","paths":["/…/continuity_<uuid>.jpg", …]}
//     {"status":"error","message":"…"}
//
// Exit codes:
//     0   capture succeeded
//     1   user cancelled (closed window or dismissed menu)
//     2   error (no iPhone found, save failure, etc.)
//
// Architecture notes:
// - We rely on AppKit's NSMenuItem.importFromDeviceIdentifier mechanism:
//   adding an empty NSMenu and popping it up causes AppKit to walk the
//   responder chain looking for any NSResponder that returns a valid
//   requestor for image-type pasteboard data. We register ourselves as
//   that requestor, so AppKit auto-inserts the iPhone capture items into
//   our menu before showing it. When the user picks one, AppKit calls our
//   readSelection(from:) with the resulting image(s) on the pasteboard.
// - This path is fully documented (see "Supporting Continuity Camera in
//   Your Mac App" in the AppKit docs) and works on macOS 12+ including
//   Tahoe (macOS 26). Unlike the older AVCaptureDevice path it does NOT
//   require camera TCC on the Mac side — the iPhone owns the camera —
//   though we keep the entitlement so --list mode keeps working.
// - Multi-page Scan Documents: the iPhone batches every page into one
//   pasteboard payload before returning. A single sidecar invocation
//   therefore yields N image paths in `paths`, matching the existing
//   contract used by the Tauri Rust caller and the frontend session UI.

import AppKit
import AVFoundation
import Foundation
import UniformTypeIdentifiers

// ─── Output helpers ──────────────────────────────────────────────────────────

func emitJSON(_ dict: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: dict, options: []),
          let str  = String(data: data, encoding: .utf8) else {
        print("{\"status\":\"error\",\"message\":\"failed to serialize output\"}")
        return
    }
    print(str)
}

func emitErrorAndExit(_ message: String, code: Int32 = 2) -> Never {
    emitJSON(["status": "error", "message": message])
    exit(code)
}

/// Diagnostic line on stderr — Tauri's stderr sink logs these. Keeps stdout
/// clean for the single JSON line the Rust side parses.
func debug(_ msg: String) {
    if let data = "[continuity-camera] \(msg)\n".data(using: .utf8) {
        FileHandle.standardError.write(data)
    }
}

// ─── --list mode ─────────────────────────────────────────────────────────────
//
// Enumerates AVCaptureDevices and emits them as JSON. Still uses AVFoundation
// because it's the only API that exposes per-device metadata (uniqueID,
// modelID, manufacturer) that the frontend's diagnostic UI shows. The
// --list call also has the side benefit of triggering the camera TCC prompt
// on first run, so the user grants permission before they hit the capture
// flow (which itself doesn't require camera permission, but a denied
// camera grant means --list returns no devices, which is misleading).

func cameraInfoDict(_ d: AVCaptureDevice) -> [String: Any] {
    let typeStr: String = {
        switch d.deviceType {
        case .continuityCamera:        return "continuity"
        case .external:                return "external"
        case .builtInWideAngleCamera:  return "builtin"
        default:                       return d.deviceType.rawValue
        }
    }()
    return [
        "id":           d.uniqueID,
        "name":         d.localizedName,
        "manufacturer": d.manufacturer,
        "modelID":      d.modelID,
        "type":         typeStr,
        "isContinuity": d.deviceType == .continuityCamera,
    ]
}

func runListMode() -> Never {
    func enumerate() -> Never {
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.continuityCamera, .external, .builtInWideAngleCamera],
            mediaType: .video,
            position: .unspecified
        )
        let cams = discovery.devices.map { cameraInfoDict($0) }
        debug("--list mode: AVFoundation found \(cams.count) camera(s)")
        emitJSON(["status": "ok", "cameras": cams])
        exit(0)
    }

    switch AVCaptureDevice.authorizationStatus(for: .video) {
    case .authorized:
        enumerate()
    case .notDetermined:
        debug("--list mode: requesting camera authorization")
        let sema = DispatchSemaphore(value: 0)
        AVCaptureDevice.requestAccess(for: .video) { _ in sema.signal() }
        sema.wait()
        enumerate()
    case .denied, .restricted:
        // Empty success rather than error — UI can distinguish "no cameras"
        // from "permission denied" via the warning field.
        emitJSON([
            "status": "ok",
            "cameras": [],
            "warning": "camera permission denied — grant LocalBook camera access in System Settings → Privacy & Security → Camera",
        ])
        exit(0)
    @unknown default:
        emitJSON(["status": "ok", "cameras": []])
        exit(0)
    }
}

// ─── Argument parsing ────────────────────────────────────────────────────────

let argv = Array(CommandLine.arguments.dropFirst())

if argv.first == "--list" {
    runListMode()
}

guard let outArg = argv.first, !outArg.hasPrefix("--") else {
    emitErrorAndExit(
        "usage: continuity-camera <output_dir> [--camera <id>] [--include-non-continuity]\n" +
        "       continuity-camera --list"
    )
}

// Legacy flags from the AVCaptureDevice era. We accept them silently for
// backward compatibility with the bundled Rust caller (lib.rs may pass
// --camera) but they no longer have any effect: AppKit's import-from-
// device flow lets the user pick the device on their iPhone screen.
do {
    var i = 1
    while i < argv.count {
        let a = argv[i]
        if a == "--camera", i + 1 < argv.count {
            debug("ignoring legacy --camera \(argv[i + 1]) (iPhone picks the device)")
            i += 2
        } else if a == "--include-non-continuity" {
            debug("ignoring legacy --include-non-continuity (not applicable to import-from-device flow)")
            i += 1
        } else {
            debug("ignoring unknown arg: \(a)")
            i += 1
        }
    }
}

let outputDir = URL(fileURLWithPath: outArg, isDirectory: true)
do {
    try FileManager.default.createDirectory(
        at: outputDir,
        withIntermediateDirectories: true,
        attributes: nil
    )
} catch {
    emitErrorAndExit("failed to create output dir: \(error.localizedDescription)")
}

// ─── ImportFromDeviceView ────────────────────────────────────────────────────
//
// NSView that AppKit consults during contextual-menu population to decide
// whether to insert iPhone capture items. Two things have to be true for
// the import-from-device flow to work:
//
//   (1) The menu must contain at least one NSMenuItem whose identifier is
//       `NSMenuItem.importFromDeviceIdentifier`. AppKit replaces this
//       placeholder with one item per available iPhone capture mode
//       ("Take Photo from iPhone", "Scan Documents from iPhone", etc.)
//       when the menu opens. Without the placeholder you get an empty
//       menu that shows generic Services items and never reaches the
//       iPhone — that was the bug in the first cut of this sidecar.
//
//   (2) An object reachable from the key window's first responder must
//       return a valid requestor for image-typed pasteboard data via
//       `validRequestor(forSendType:returnType:)`. We make THIS view the
//       window's first responder so AppKit's responder-chain walk hits
//       our validRequestor on the first hop.
//
// The view is intentionally minimal-visual: it just hosts a status label
// and exists primarily for its responder-chain role.

final class ImportFromDeviceView: NSView, NSServicesMenuRequestor {
    weak var controller: ImportFromDeviceController?

    override var acceptsFirstResponder: Bool { true }

    override func validRequestor(
        forSendType sendType: NSPasteboard.PasteboardType?,
        returnType: NSPasteboard.PasteboardType?
    ) -> Any? {
        if let returnType = returnType,
           NSImage.imageTypes.contains(returnType.rawValue) {
            return self
        }
        return super.validRequestor(forSendType: sendType, returnType: returnType)
    }

    // NSServicesMenuRequestor — AppKit calls this when the user picks an
    // iPhone capture mode and the iPhone returns image data via the
    // system pasteboard.
    func readSelection(from pasteboard: NSPasteboard) -> Bool {
        controller?.handleReadSelection(from: pasteboard) ?? false
    }

    // Protocol-required but unused — we never send data outward.
    func writeSelection(to pboard: NSPasteboard, types: [NSPasteboard.PasteboardType]) -> Bool {
        false
    }
}

// ─── ImportFromDeviceController ──────────────────────────────────────────────
//
// Owns the launcher window and the lifecycle of one capture session. The
// flow is intentionally one Mac click long: the user picks Continuity
// Camera in the LocalBook scan menu → the sidecar launches and shows a
// small "Connecting to iPhone…" window → the import-from-device menu auto-
// pops centred on that window → the user taps Take Photo or Scan Documents
// directly on their iPhone screen → the image flows back via readSelection
// and the sidecar exits. No extra Mac button to click.

final class ImportFromDeviceController: NSObject, NSWindowDelegate, NSMenuDelegate {
    private let outputDir: URL
    private var window: NSWindow!
    private var view: ImportFromDeviceView!
    private var statusLabel: NSTextField!
    private var menu: NSMenu?
    private var finalized = false
    private var menuOpened = false

    init(outputDir: URL) {
        self.outputDir = outputDir
        super.init()
    }

    func start() {
        DispatchQueue.main.async { self.show() }
    }

    private func show() {
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 180),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        win.title = "LocalBook — Insert from iPhone"
        win.center()
        win.delegate = self
        win.isReleasedWhenClosed = false

        let content = ImportFromDeviceView(frame: win.contentLayoutRect)
        content.controller = self

        let heading = NSTextField(labelWithString: "Insert from iPhone")
        heading.font = .systemFont(ofSize: 16, weight: .semibold)
        heading.frame = NSRect(x: 20, y: 130, width: 380, height: 24)
        heading.alignment = .center
        content.addSubview(heading)

        statusLabel = NSTextField(wrappingLabelWithString: "Connecting to your iPhone…")
        statusLabel.frame = NSRect(x: 24, y: 70, width: 372, height: 50)
        statusLabel.alignment = .center
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.font = .systemFont(ofSize: 12)
        content.addSubview(statusLabel)

        let cancelButton = NSButton(
            title: "Cancel",
            target: self,
            action: #selector(cancelClicked(_:))
        )
        cancelButton.frame = NSRect(x: 170, y: 16, width: 80, height: 28)
        cancelButton.bezelStyle = .rounded
        cancelButton.keyEquivalent = "\u{1b}"  // Escape
        content.addSubview(cancelButton)

        win.contentView = content
        self.view = content

        // LSUIElement keeps us out of the Dock by default. We need
        // foreground activation now so the window comes to the front and
        // the auto-popped menu actually appears on screen.
        NSApp.setActivationPolicy(.regular)
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        // CRITICAL: the import-from-device flow walks the key window's
        // responder chain looking for a valid requestor for image
        // pasteboard data. Make our view the first responder so the walk
        // hits ImportFromDeviceView.validRequestor on hop one.
        win.makeFirstResponder(content)

        self.window = win

        // Auto-pop the iPhone menu — no manual Mac click needed.
        // 100ms gives AppKit time to finish wiring up the responder chain
        // and the foreground activation transition.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
            self?.popImportFromDeviceMenu()
        }
    }

    // ── popImportFromDeviceMenu ──────────────────────────────────────────
    //
    // Builds the menu containing the magic placeholder item, then pops it
    // up at the centre of the launcher window. The placeholder triggers
    // AppKit's auto-population: each available iPhone capture mode is
    // inserted as a separate item before display. If no iPhone is
    // available the menu shows up empty/dismissed, which we report via
    // the status label.

    private func popImportFromDeviceMenu() {
        guard !finalized, let window = window, let view = view else { return }

        let menu = NSMenu(title: "Insert from iPhone")
        menu.delegate = self
        menu.autoenablesItems = true

        // The placeholder. AppKit identifies it by its
        // NSUserInterfaceItemIdentifier and replaces it (in place) with
        // per-device, per-capture-mode items when the menu opens.
        let placeholder = NSMenuItem()
        placeholder.title = "Insert from iPhone"
        placeholder.identifier = NSMenuItem.importFromDeviceIdentifier
        menu.addItem(placeholder)

        self.menu = menu

        // popUpContextMenu requires a real NSEvent. We synthesise a
        // mouse event at the centre of our content view.
        let centre = NSPoint(x: view.bounds.midX, y: view.bounds.midY)
        let event = NSEvent.mouseEvent(
            with: .leftMouseUp,
            location: view.convert(centre, to: nil),
            modifierFlags: [],
            timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber,
            context: nil,
            eventNumber: 0,
            clickCount: 1,
            pressure: 0
        )

        statusLabel.stringValue = "Pick a capture mode below — the rest happens on your iPhone."

        guard let event = event else {
            finalize(error: "Failed to synthesise menu event.")
            return
        }

        NSMenu.popUpContextMenu(menu, with: event, for: view)

        // popUpContextMenu blocks until the menu closes. By the time we
        // return here either readSelection() has already fired and
        // finalised the sidecar, OR the user dismissed the menu. We use
        // menuOpened (set by NSMenuDelegate.menuWillOpen) to distinguish
        // "AppKit never even showed the menu" (no iPhone found) from
        // "menu shown and dismissed without picking".
        if finalized { return }

        if !menuOpened {
            statusLabel.stringValue =
                "No iPhone detected. Make sure it's unlocked, on the same Apple ID, " +
                "and within range. Close this window and try again."
        } else {
            // Menu was shown but user dismissed without picking. Re-pop
            // automatically so they can try again without an extra click
            // on a Mac button.
            statusLabel.stringValue = "Cancelled — pick a mode or click Cancel to close."
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in
                guard let self, !self.finalized else { return }
                self.menuOpened = false
                self.popImportFromDeviceMenu()
            }
        }
    }

    // NSMenuDelegate — used to detect whether AppKit actually opened the
    // menu (it skips the open if no iPhone is around).
    func menuWillOpen(_ menu: NSMenu) {
        menuOpened = true
        debug("import-from-device menu opening with \(menu.numberOfItems) item(s)")
    }

    @objc private func cancelClicked(_ sender: NSButton) {
        finalize(error: "User cancelled.", code: 1)
    }

    // ── Pasteboard → disk (called by ImportFromDeviceView) ───────────────

    fileprivate func handleReadSelection(from pasteboard: NSPasteboard) -> Bool {
        debug("readSelection: types=\(pasteboard.types?.map(\.rawValue) ?? [])")
        let paths = saveImagesFromPasteboard(pasteboard)
        if paths.isEmpty {
            debug("readSelection: pasteboard had no extractable image data")
            statusLabel?.stringValue = "No image data returned. Try again."
            return false
        }
        debug("readSelection: saved \(paths.count) image(s) to \(outputDir.path)")
        finalize(success: paths)
        return true
    }

    // ── Pasteboard → disk ────────────────────────────────────────────────
    //
    // Continuity Camera delivers image data in one of two shapes depending
    // on capture mode and macOS version:
    //   1. File URLs (NSURL) pointing at temp files — common for Scan
    //      Documents which can produce N pages and benefits from on-disk
    //      delivery to avoid carrying N NSImages on the pasteboard.
    //   2. NSImage instances — common for Take Photo and Add Sketch.
    // We try (1) first, then fall through to (2). Both paths produce the
    // same uniform JPEG output that the OCR pipeline expects.

    private func saveImagesFromPasteboard(_ pb: NSPasteboard) -> [String] {
        var paths: [String] = []

        if let urls = pb.readObjects(forClasses: [NSURL.self], options: nil) as? [URL] {
            for url in urls where url.isFileURL {
                if let path = copyImageFile(url) { paths.append(path) }
            }
            if !paths.isEmpty { return paths }
        }

        if let images = pb.readObjects(forClasses: [NSImage.self], options: nil) as? [NSImage] {
            for image in images {
                if let path = saveImageAsJpeg(image) { paths.append(path) }
            }
        }

        return paths
    }

    private func copyImageFile(_ src: URL) -> String? {
        let ext = src.pathExtension.isEmpty ? "jpg" : src.pathExtension
        let dst = outputDir.appendingPathComponent("continuity_\(UUID().uuidString).\(ext)")
        do {
            try FileManager.default.copyItem(at: src, to: dst)
            return dst.path
        } catch {
            debug("copyImageFile(\(src.path)) failed: \(error.localizedDescription)")
            return nil
        }
    }

    private func saveImageAsJpeg(_ image: NSImage) -> String? {
        // NSBitmapImageRep handles orientation/colour-space conversion for
        // us; the resulting bitmap is pixel-upright with no EXIF rotation
        // tag needed downstream.
        guard let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let jpeg = bitmap.representation(
                using: .jpeg,
                properties: [.compressionFactor: 0.92]
              )
        else {
            return nil
        }
        let dst = outputDir.appendingPathComponent("continuity_\(UUID().uuidString).jpg")
        do {
            try jpeg.write(to: dst)
            return dst.path
        } catch {
            debug("saveImageAsJpeg failed: \(error.localizedDescription)")
            return nil
        }
    }

    // ── Finalisation ─────────────────────────────────────────────────────

    private func finalize(success paths: [String]) {
        guard !finalized else { return }
        finalized = true
        emitJSON(["status": "ok", "paths": paths])
        DispatchQueue.main.async { [weak self] in
            self?.window?.close()
            exit(0)
        }
    }

    private func finalize(error message: String, code: Int32 = 2) {
        guard !finalized else { return }
        finalized = true
        emitJSON(["status": "error", "message": message])
        DispatchQueue.main.async { [weak self] in
            self?.window?.close()
            exit(code)
        }
    }

    // ── NSWindowDelegate ─────────────────────────────────────────────────

    func windowWillClose(_ notification: Notification) {
        if !finalized {
            finalize(error: "User closed the capture window.", code: 1)
        }
    }
}

// ─── AppDelegate ─────────────────────────────────────────────────────────────

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: ImportFromDeviceController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller = ImportFromDeviceController(outputDir: outputDir)
        controller.start()

        // Absolute safety timeout — 5 minutes from launch. Catches stuck
        // iPhone-side flows where the user walked away mid-capture.
        DispatchQueue.main.asyncAfter(deadline: .now() + 300) {
            emitJSON([
                "status": "error",
                "message": "Timed out after 5 minutes waiting for iPhone capture.",
            ])
            exit(1)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}

// ─── Entry point ─────────────────────────────────────────────────────────────

let delegate = AppDelegate()
let app = NSApplication.shared
app.delegate = delegate
app.run()
