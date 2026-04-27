// ContinuityCamera.swift
//
// CLI helper that shows a live iPhone camera preview (via Continuity Camera)
// and captures a single photo per invocation. The captured image is saved to
// the directory passed as the first argument. The absolute path is emitted
// on stdout as a single JSON line:
//
//     {"status":"ok","paths":["/…/continuity_<uuid>.jpg"]}
//
// On cancellation / failure, prints:
//
//     {"status":"error","message":"…"}
//
// Exit codes: 0 on success, 1 on user-cancel, 2 on error.
//
// Usage:
//     continuity-camera <output_dir>
//
// Uses AVCaptureDevice.DiscoverySession with .continuityCamera device type.
// On macOS 15+/26 Tahoe, Apple removed both the Services-menu pathway
// (NSPerformService "Capture.ImportImage") and ImageCaptureCore/
// ICDeviceBrowser visibility for Continuity iPhones. AVFoundation is the
// only remaining public path. Multi-page accumulation is handled
// frontend-side in Sprint 8's scan session UI — each sidecar invocation
// captures exactly one image.
//
// Signed with com.apple.security.device.camera entitlement (adhoc OK for
// local dev, Developer ID for distribution).

import AppKit
import AVFoundation
import Foundation
import UniformTypeIdentifiers

// ─── Output helpers ───────────────────────────────────────────────────────────

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

/// Write a diagnostic line to stderr so Tauri's stderr sink can log it.
/// Keeps stdout clean for the final JSON payload the Rust side parses.
func debug(_ msg: String) {
    if let data = "[continuity-camera] \(msg)\n".data(using: .utf8) {
        FileHandle.standardError.write(data)
    }
}

// ─── Argument parsing ─────────────────────────────────────────────────────────
//
// Modes:
//   continuity-camera --list
//       Enumerate every camera AVFoundation can see and emit the result as
//       JSON on stdout, then exit. No window is shown, no capture happens.
//       Used by the frontend to populate a camera picker when more than one
//       device is available.
//
//   continuity-camera <output_dir> [--camera <uniqueID>] [--include-non-continuity]
//       Original capture mode. If --camera <uid> is given, that specific
//       device is selected (matched by AVCaptureDevice.uniqueID); otherwise
//       the first .continuityCamera is used. --include-non-continuity loosens
//       the strict filter so a built-in / external camera can be used as a
//       fallback when no iPhone is paired.

let argv = Array(CommandLine.arguments.dropFirst())

// ─── --list mode ─────────────────────────────────────────────────────────────

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
    // Camera authorization is required for AVFoundation to surface device
    // metadata on macOS 14+. We request it here too — same as capture mode —
    // so a first-time --list call triggers the standard TCC prompt rather
    // than silently returning an empty list.
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
        // We still emit an empty success rather than an error so the UI can
        // distinguish "no cameras attached" from "permission denied".
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

if argv.first == "--list" {
    runListMode()
}

// ─── Capture mode arg parsing ────────────────────────────────────────────────

guard let outArg = argv.first, !outArg.hasPrefix("--") else {
    emitErrorAndExit(
        "usage: continuity-camera <output_dir> [--camera <uniqueID>] [--include-non-continuity]\n" +
        "       continuity-camera --list"
    )
}

var preferredCameraID: String? = nil
var includeNonContinuity = false
do {
    var i = 1
    while i < argv.count {
        let a = argv[i]
        if a == "--camera", i + 1 < argv.count {
            preferredCameraID = argv[i + 1]
            i += 2
        } else if a == "--include-non-continuity" {
            includeNonContinuity = true
            i += 1
        } else {
            // Unknown flag — log and skip rather than fail, so older callers
            // passing future args don't break this binary.
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

// ─── AVCaptureDevice-based Continuity Camera flow ─────────────────────────────
//
// 1. Prompt for camera authorization (TCC) if not yet granted.
// 2. Discover AVCaptureDevices with type .continuityCamera — the user's
//    paired iPhone appears here once it's unlocked and nearby.
// 3. Wire it into an AVCaptureSession with a photo output.
// 4. Present a small window with a live preview layer + Capture / Cancel.
// 5. On Capture: take a still photo, write it to outputDir, emit JSON, exit.
// 6. 5-minute absolute timeout prevents hangs.

final class CaptureController: NSObject {
    private let session = AVCaptureSession()
    private let photoOutput = AVCapturePhotoOutput()
    private var previewLayer: AVCaptureVideoPreviewLayer!
    private var window: NSWindow!
    private var statusLabel: NSTextField!
    private var captureButton: NSButton!
    private var finalized = false
    private let outputDir: URL
    private let preferredCameraID: String?
    private let includeNonContinuity: Bool

    init(outputDir: URL, preferredCameraID: String? = nil, includeNonContinuity: Bool = false) {
        self.outputDir = outputDir
        self.preferredCameraID = preferredCameraID
        self.includeNonContinuity = includeNonContinuity
        super.init()
    }

    func start() {
        requestAuthorization { [weak self] granted in
            guard let self = self else { return }
            if !granted {
                self.finish(error: "Camera permission denied. Grant LocalBook camera access in System Settings → Privacy & Security → Camera.")
                return
            }
            DispatchQueue.main.async {
                self.discoverAndShow()
            }
        }
    }

    /// AVCaptureDevice .continuityCamera requires standard camera TCC grant.
    private func requestAuthorization(_ completion: @escaping (Bool) -> Void) {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            completion(true)
        case .notDetermined:
            debug("requesting camera authorization")
            AVCaptureDevice.requestAccess(for: .video) { completion($0) }
        case .denied, .restricted:
            completion(false)
        @unknown default:
            completion(false)
        }
    }

    /// Discover Continuity Camera devices and wire up the session + window.
    private func discoverAndShow() {
        // We discover BUILTIN + EXTERNAL + CONTINUITY so the diagnostic log
        // can show every camera macOS sees (including stranded virtual
        // cameras like mmhmm). But we ONLY accept .continuityCamera as a
        // capture target — falling back to "first device" let mmhmm hijack
        // the session on at least one user machine.
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [
                .continuityCamera,
                .external,
                .builtInWideAngleCamera,
            ],
            mediaType: .video,
            position: .unspecified
        )
        let devices = discovery.devices
        debug("AVCaptureDevice discovery found \(devices.count) device(s):")
        if devices.isEmpty {
            debug("  (none — AVFoundation returned an empty device list)")
        }
        for d in devices {
            let isContinuity = d.deviceType == .continuityCamera ? " [CONTINUITY]" : ""
            debug("  • \(d.localizedName) — type=\(d.deviceType.rawValue) " +
                  "manufacturer=\(d.manufacturer) modelID=\(d.modelID) " +
                  "uniqueID=\(d.uniqueID)\(isContinuity)")
        }

        // Selection priority:
        //   1. If --camera <uid> was passed, use that exact device (any type).
        //      The user has explicitly opted in via the picker UI.
        //   2. Else: first .continuityCamera (the iPhone — preferred default).
        //   3. Else, only when --include-non-continuity is set: first external,
        //      then first builtInWideAngleCamera. By default we still refuse
        //      to fall through to a virtual camera.
        let chosen: AVCaptureDevice? = {
            if let uid = preferredCameraID, !uid.isEmpty {
                if let exact = devices.first(where: { $0.uniqueID == uid }) {
                    debug("using user-selected camera: \(exact.localizedName) (\(uid))")
                    return exact
                }
                debug("requested --camera \(uid) not found; falling back to default selection")
            }
            if let cont = devices.first(where: { $0.deviceType == .continuityCamera }) {
                return cont
            }
            if includeNonContinuity {
                if let ext = devices.first(where: { $0.deviceType == .external }) {
                    debug("falling back to external camera: \(ext.localizedName)")
                    return ext
                }
                if let bi = devices.first(where: { $0.deviceType == .builtInWideAngleCamera }) {
                    debug("falling back to built-in camera: \(bi.localizedName)")
                    return bi
                }
            }
            return nil
        }()

        guard let device = chosen else {
            let nonContinuityCount = devices.count
            var msg = "No iPhone or iPad found via Continuity Camera.\n\n"
            msg += "Checklist:\n"
            msg += "  • iPhone unlocked and within ~30 ft of this Mac\n"
            msg += "  • Same Apple ID on both, Wi-Fi + Bluetooth on, same network\n"
            msg += "  • iPhone: Settings → General → AirPlay & Continuity → "
            msg += "Continuity Camera = ON\n"
            msg += "  • Mac: System Settings → General → AirDrop & Handoff → "
            msg += "Allow Handoff = ON\n\n"
            if nonContinuityCount > 0 {
                msg += "AVFoundation did see \(nonContinuityCount) other camera(s) "
                msg += "(see stderr log) but none is a Continuity device.\n"
                msg += "If a virtual camera (mmhmm, OBS, etc.) is in that list and the "
                msg += "app is uninstalled, remove its leftover plugin from "
                msg += "/Library/CoreMediaIO/Plug-Ins/DAL/ and reboot."
            } else {
                msg += "AVFoundation returned ZERO cameras. Camera permission is "
                msg += "likely denied, OR the embedded NSCameraUseContinuityCameraDeviceType "
                msg += "key isn't being read by this build.\n\n"
                msg += "Fix — open System Settings → Privacy & Security → Camera, "
                msg += "and make sure LocalBook is toggled ON.\n"
                msg += "If LocalBook isn't listed there yet, reset and relaunch:\n"
                msg += "  tccutil reset Camera com.localbook.desktop\n"
                msg += "  tccutil reset Camera com.localbook.continuity-camera\n"
                msg += "  tccutil reset Camera   # nuclear — resets ALL apps\n"
                msg += "(the first two may report 'no such bundle identifier' if "
                msg += "TCC has never seen them; that's fine, the reset still clears "
                msg += "any stale deny-decision.) Then relaunch LocalBook and say "
                msg += "YES to the camera prompt."
            }
            finish(error: msg)
            return
        }

        debug("selected Continuity Camera: \(device.localizedName) (\(device.modelID))")

        do {
            let input = try AVCaptureDeviceInput(device: device)
            session.beginConfiguration()
            session.sessionPreset = .photo
            if session.canAddInput(input) { session.addInput(input) }
            if session.canAddOutput(photoOutput) { session.addOutput(photoOutput) }
            session.commitConfiguration()
        } catch {
            finish(error: "Failed to open iPhone camera: \(error.localizedDescription)")
            return
        }

        buildWindow()
        session.startRunning()
    }

    /// Build a small borderless-titled window with preview + buttons.
    private func buildWindow() {
        let frame = NSRect(x: 0, y: 0, width: 640, height: 520)
        window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "LocalBook: Scan with iPhone"
        window.center()
        window.isReleasedWhenClosed = false
        window.delegate = self

        let content = NSView(frame: frame)
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.black.cgColor

        // Preview layer
        previewLayer = AVCaptureVideoPreviewLayer(session: session)
        previewLayer.frame = NSRect(x: 0, y: 70, width: 640, height: 450)
        previewLayer.videoGravity = .resizeAspect
        content.layer?.addSublayer(previewLayer)

        // Status label
        statusLabel = NSTextField(labelWithString: "Frame your document on your iPhone, then click Capture.")
        statusLabel.frame = NSRect(x: 12, y: 44, width: 616, height: 20)
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.font = .systemFont(ofSize: 12)
        content.addSubview(statusLabel)

        // Cancel button
        let cancelButton = NSButton(frame: NSRect(x: 12, y: 8, width: 110, height: 32))
        cancelButton.title = "Cancel"
        cancelButton.bezelStyle = .rounded
        cancelButton.target = self
        cancelButton.action = #selector(cancelPressed)
        content.addSubview(cancelButton)

        // Capture button
        captureButton = NSButton(frame: NSRect(x: 518, y: 8, width: 110, height: 32))
        captureButton.title = "Capture"
        captureButton.bezelStyle = .rounded
        captureButton.keyEquivalent = "\r"
        captureButton.target = self
        captureButton.action = #selector(capturePressed)
        content.addSubview(captureButton)

        window.contentView = content
        NSApp.setActivationPolicy(.regular)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func capturePressed() {
        captureButton.isEnabled = false
        statusLabel.stringValue = "Capturing…"

        let settings = AVCapturePhotoSettings()
        if photoOutput.availablePhotoCodecTypes.contains(.jpeg) {
            // Default codec is fine; leaving settings default produces HEVC/HEIC
            // on some devices. Force JPEG for pipeline compatibility.
            let jpegSettings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
            photoOutput.capturePhoto(with: jpegSettings, delegate: self)
        } else {
            photoOutput.capturePhoto(with: settings, delegate: self)
        }
    }

    @objc private func cancelPressed() {
        finish(error: "User cancelled capture.", code: 1)
    }

    fileprivate func finish(error: String? = nil, code: Int32 = 2, paths: [String] = []) {
        guard !finalized else { return }
        finalized = true

        if session.isRunning { session.stopRunning() }
        window?.orderOut(nil)

        if let err = error {
            emitJSON(["status": "error", "message": err])
            exit(code)
        }

        emitJSON(["status": "ok", "paths": paths])
        exit(0)
    }
}

// ─── AVCapturePhotoCaptureDelegate ───────────────────────────────────────────

extension CaptureController: AVCapturePhotoCaptureDelegate {
    func photoOutput(
        _ output: AVCapturePhotoOutput,
        didFinishProcessingPhoto photo: AVCapturePhoto,
        error: Error?
    ) {
        if let err = error {
            finish(error: "Capture failed: \(err.localizedDescription)")
            return
        }
        guard let data = photo.fileDataRepresentation() else {
            finish(error: "Captured photo produced no data.")
            return
        }

        let name = "continuity_\(UUID().uuidString).jpg"
        let dst = outputDir.appendingPathComponent(name, isDirectory: false)
        do {
            try data.write(to: dst)
            debug("saved → \(dst.path)")
            finish(paths: [dst.path])
        } catch {
            finish(error: "Failed to write image: \(error.localizedDescription)")
        }
    }
}

// ─── NSWindowDelegate ────────────────────────────────────────────────────────

extension CaptureController: NSWindowDelegate {
    func windowWillClose(_ notification: Notification) {
        finish(error: "User closed the capture window.", code: 1)
    }
}

// ─── AppDelegate ─────────────────────────────────────────────────────────────

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: CaptureController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        controller = CaptureController(
            outputDir: outputDir,
            preferredCameraID: preferredCameraID,
            includeNonContinuity: includeNonContinuity
        )
        controller.start()

        // Absolute safety timeout: 5 minutes from launch.
        DispatchQueue.main.asyncAfter(deadline: .now() + 300) {
            emitJSON([
                "status": "error",
                "message": "Timed out after 5 minutes waiting for Continuity Camera capture.",
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
