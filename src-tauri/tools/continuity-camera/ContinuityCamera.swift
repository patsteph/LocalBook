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

let args = CommandLine.arguments
guard args.count >= 2 else {
    emitErrorAndExit("usage: continuity-camera <output_dir>")
}

let outputDir = URL(fileURLWithPath: args[1], isDirectory: true)

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

    init(outputDir: URL) {
        self.outputDir = outputDir
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
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.continuityCamera, .external],
            mediaType: .video,
            position: .unspecified
        )
        let devices = discovery.devices
        debug("AVCaptureDevice discovery found \(devices.count) device(s)")
        for d in devices {
            debug("  device: \(d.localizedName) uniqueID=\(d.uniqueID) type=\(d.deviceType.rawValue)")
        }

        guard let device = devices.first(where: { $0.deviceType == .continuityCamera })
                      ?? devices.first else {
            finish(error: "No iPhone or iPad found via Continuity Camera. Make sure your device is unlocked, nearby, signed in to the same Apple ID, and both Wi-Fi and Bluetooth are on.")
            return
        }

        debug("selected device: \(device.localizedName)")

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
        controller = CaptureController(outputDir: outputDir)
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
