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

// ─── ImportFromDeviceController ──────────────────────────────────────────────
//
// Owns the launcher window, registers itself as the responder that accepts
// image pasteboard data, and finalises the sidecar run when the iPhone
// returns image(s) (or the user cancels).

final class ImportFromDeviceController: NSResponder, NSWindowDelegate, NSServicesMenuRequestor {
    private let outputDir: URL
    private var window: NSWindow!
    private var statusLabel: NSTextField!
    private var captureButton: NSButton!
    private var finalized = false

    init(outputDir: URL) {
        self.outputDir = outputDir
        super.init()
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    func start() {
        DispatchQueue.main.async { self.show() }
    }

    private func show() {
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 440, height: 220),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        win.title = "LocalBook — Insert from iPhone"
        win.center()
        win.delegate = self
        win.isReleasedWhenClosed = false

        let content = NSView(frame: win.contentLayoutRect)

        // Heading
        let heading = NSTextField(labelWithString: "Capture from your iPhone")
        heading.font = .systemFont(ofSize: 16, weight: .semibold)
        heading.frame = NSRect(x: 20, y: 168, width: 400, height: 24)
        heading.alignment = .center
        content.addSubview(heading)

        // Instruction
        statusLabel = NSTextField(wrappingLabelWithString:
            "Click below, then choose Take Photo, Scan Documents, or Add Sketch on your iPhone screen.")
        statusLabel.frame = NSRect(x: 24, y: 100, width: 392, height: 60)
        statusLabel.alignment = .center
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.font = .systemFont(ofSize: 12)
        content.addSubview(statusLabel)

        // Primary action — pops up the iPhone capture menu.
        captureButton = NSButton(
            title: "Capture from iPhone…",
            target: self,
            action: #selector(captureClicked(_:))
        )
        captureButton.frame = NSRect(x: 140, y: 50, width: 160, height: 32)
        captureButton.bezelStyle = .rounded
        captureButton.keyEquivalent = "\r"  // Enter triggers
        content.addSubview(captureButton)

        // Cancel — Esc shortcut for keyboard users.
        let cancelButton = NSButton(
            title: "Cancel",
            target: self,
            action: #selector(cancelClicked(_:))
        )
        cancelButton.frame = NSRect(x: 20, y: 14, width: 80, height: 24)
        cancelButton.bezelStyle = .rounded
        cancelButton.keyEquivalent = "\u{1b}"  // Escape
        content.addSubview(cancelButton)

        win.contentView = content

        // Hook ourselves into the responder chain. AppKit walks the chain
        // calling validRequestor(forSendType:returnType:) when populating
        // the import-from-device menu; without us in the chain it would
        // never auto-fill the iPhone items.
        self.nextResponder = content.nextResponder
        content.nextResponder = self

        // LSUIElement keeps us out of the Dock by default. We need
        // foreground activation while the launcher window is up so the
        // window comes to the front and key events route correctly.
        NSApp.setActivationPolicy(.regular)
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        self.window = win
    }

    // ── Capture button → contextual menu ─────────────────────────────────
    //
    // Builds an empty NSMenu and pops it up at the button. AppKit detects
    // that an NSResponder in the chain (this controller) accepts image-
    // type pasteboard data and auto-inserts iPhone capture items. The
    // user picks one; AppKit shows the iPhone-side UI; the captured
    // image(s) come back via readSelection(from:) below.

    @objc private func captureClicked(_ sender: NSButton) {
        let menu = NSMenu(title: "")
        statusLabel.stringValue = "Look at your iPhone — pick a capture mode."

        // popUpContextMenu requires a real NSEvent. The button's click
        // event is the most recent NSApp.currentEvent (a left-mouse-up).
        // Synthesize one if for some reason the runtime didn't surface
        // one (defensive — should never happen for a button click).
        let event: NSEvent = NSApp.currentEvent ?? NSEvent.mouseEvent(
            with: .leftMouseUp,
            location: sender.convert(
                NSPoint(x: sender.bounds.midX, y: sender.bounds.midY),
                to: nil
            ),
            modifierFlags: [],
            timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber,
            context: nil,
            eventNumber: 0,
            clickCount: 1,
            pressure: 0
        )!

        NSMenu.popUpContextMenu(menu, with: event, for: sender)

        // After the popup returns, readSelection(from:) may have already
        // fired and finalised the run on a different runloop turn. If we
        // got here without finalising, the user either dismissed the
        // menu or the menu was empty (no iPhone available). Update the
        // status label for both cases.
        if finalized { return }
        if menu.numberOfItems == 0 {
            statusLabel.stringValue =
                "No iPhone detected. Make sure it's unlocked, on the same Apple ID, " +
                "and within range — then click Capture again."
        } else {
            statusLabel.stringValue = "Click Capture to try again, or Cancel to close."
        }
    }

    @objc private func cancelClicked(_ sender: NSButton) {
        finalize(error: "User cancelled.", code: 1)
    }

    // ── NSResponder: declare we accept image pasteboard data ─────────────
    //
    // AppKit calls this on every responder in the chain when populating
    // the import-from-device menu. Returning `self` for image return-
    // types tells AppKit "this responder will receive the captured
    // image" — and AppKit then knows to populate the menu with iPhone
    // capture items.

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

    // ── NSServicesMenuRequestor: receive the captured image(s) ───────────

    func readSelection(from pasteboard: NSPasteboard) -> Bool {
        debug("readSelection: types=\(pasteboard.types?.map(\.rawValue) ?? [])")
        let paths = saveImagesFromPasteboard(pasteboard)
        if paths.isEmpty {
            debug("readSelection: pasteboard had no extractable image data")
            statusLabel?.stringValue =
                "No image data was returned. Try a different capture mode on your iPhone."
            return false
        }
        debug("readSelection: saved \(paths.count) image(s) to \(outputDir.path)")
        finalize(success: paths)
        return true
    }

    /// Required by NSServicesMenuRequestor protocol but unused — we never
    /// send selection data outward.
    func writeSelection(
        to pboard: NSPasteboard,
        types: [NSPasteboard.PasteboardType]
    ) -> Bool {
        false
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
