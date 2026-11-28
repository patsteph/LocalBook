import AppKit
import LocalAuthentication
import LocalBookCore
import LocalBookEngines
import ServiceManagement
import SwiftUI

/// Protecting this Mac's copy of the library, and keeping the agents working when the window is closed.
///
/// The library lives in the app's sandbox container, so the disk it sits on is what encrypts it at rest: FileVault,
/// which macOS turns on by default on Apple silicon. The old app's own encrypted volume (an APFS sparsebundle mounted
/// with `hdiutil`) cannot be built here: a sandboxed app cannot start the disk-image daemon (`hdiejectd`, "Device not
/// configured" / "Operation not permitted"), and Apple's own guidance is not to try. So the Mac app says what covers
/// the data instead of pretending, and adds the lock the old app never had: Touch ID (or the login password) to open
/// LocalBook, and again after it has been left alone.
@MainActor @Observable
final class AppLock {
    static let enabledKey = "LocalBook.touchIDLock"
    static let minutesKey = "LocalBook.lockAfterMinutes"

    private(set) var locked = false
    private(set) var checking = false
    private(set) var problem: String?
    private var leftAt: Date?
    private var observers: [NSObjectProtocol] = []

    var enabled: Bool {
        get { UserDefaults.standard.bool(forKey: Self.enabledKey) }
        set { UserDefaults.standard.set(newValue, forKey: Self.enabledKey); if !newValue { locked = false } }
    }
    /// 0 means "only when LocalBook opens".
    var lockAfterMinutes: Int {
        get { UserDefaults.standard.object(forKey: Self.minutesKey) as? Int ?? 15 }
        set { UserDefaults.standard.set(newValue, forKey: Self.minutesKey) }
    }

    /// What this Mac can check with: Touch ID, Apple Watch, or just the password.
    static var biometryLine: String {
        let ctx = LAContext()
        var err: NSError?
        let can = ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err)
        guard can else { return "This Mac cannot ask for a sign-in here" + (err.map { ": \($0.localizedDescription)" } ?? "") }
        switch ctx.biometryType {
        case .touchID: return "Touch ID, or your login password"
        case .faceID: return "Face ID, or your login password"
        case .opticID: return "Optic ID, or your login password"
        default: return "Your login password (or Apple Watch)"
        }
    }

    /// At launch: lock if the setting is on, and watch the app leave and return.
    func start() {
        if enabled { locked = true }
        let nc = NotificationCenter.default
        observers.append(nc.addObserver(forName: NSApplication.didResignActiveNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.leftAt = Date() }
        })
        observers.append(nc.addObserver(forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.cameBack() }
        })
        observers.append(DistributedNotificationCenter.default().addObserver(forName: Notification.Name("com.apple.screenIsLocked"), object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in if let self, self.enabled { self.locked = true } }
        })
    }

    private func cameBack() {
        guard enabled, !locked, let leftAt else { return }
        let minutes = lockAfterMinutes
        if minutes > 0, Date().timeIntervalSince(leftAt) >= Double(minutes) * 60 { locked = true }
    }

    func lockNow() { if enabled { locked = true } }

    /// Asks macOS; the system sheet does the Touch ID or password part.
    func unlock() async {
        guard !checking else { return }
        checking = true; problem = nil
        defer { checking = false }
        let ctx = LAContext()
        ctx.localizedCancelTitle = "Not now"
        do {
            let ok = try await ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "open your LocalBook library")
            if ok { locked = false }
        } catch let e as LAError where e.code == .userCancel || e.code == .appCancel || e.code == .systemCancel {
            problem = nil
        } catch {
            problem = error.localizedDescription
        }
    }
}

/// What covers the whole window while LocalBook is locked. Nothing behind it is readable: the window's content is
/// hidden, not just dimmed.
struct LockScreen: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        ZStack {
            Rectangle().fill(.background)
            VStack(spacing: 14) {
                Image(systemName: "lock.fill").font(.system(size: 44)).foregroundStyle(.secondary)
                Text("LocalBook is locked").font(.title2.weight(.semibold))
                Text(AppLock.biometryLine).font(.callout).foregroundStyle(.secondary)
                Button { Task { await model.lock.unlock() } } label: { Label("Unlock", systemImage: "touchid").frame(minWidth: 120) }
                    .buttonStyle(.borderedProminent).controlSize(.large).disabled(model.lock.checking).keyboardShortcut(.defaultAction)
                if let p = model.lock.problem { Text(p).font(.caption).foregroundStyle(.red) }
            }
        }
        .task { await model.lock.unlock() }
    }
}

/// FileVault, read from the system (`fdesetup status` needs no privilege to read). Unknown when the sandbox will not
/// let the tool run; the line says so instead of guessing.
enum FileVaultStatus {
    enum State: Sendable { case on, off, unknown(String) }
    static func read() async -> State {
        await Task.detached {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/fdesetup")
            p.arguments = ["status"]
            let out = Pipe(); p.standardOutput = out; p.standardError = out
            do { try p.run() } catch { return .unknown("could not ask the system (\(error.localizedDescription))") }
            p.waitUntilExit()
            let text = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self).lowercased()
            if text.contains("filevault is on") { return .on }
            if text.contains("filevault is off") { return .off }
            return .unknown(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "no answer from the system" : String(text.prefix(120)))
        }.value
    }
}

/// Opening LocalBook at login, through the system's login items (System Settings › General › Login Items lists it).
enum LoginItem {
    static var isOn: Bool { SMAppService.mainApp.status == .enabled }
    static var needsApproval: Bool { SMAppService.mainApp.status == .requiresApproval }
    static func set(_ on: Bool) -> String? {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            return nil
        } catch { return error.localizedDescription }
    }
    static func openSettings() { SMAppService.openSystemSettingsLoginItems() }
}

/// Settings › Security › the protection and background sections.
struct ProtectionSettings: View {
    @Environment(AppModel.self) private var model
    @AppStorage(BackgroundMenu.keepRunningKey) private var keepRunning = true
    @State private var fileVault: FileVaultStatus.State = .unknown("checking…")
    @State private var loginOn = LoginItem.isOn
    @State private var loginProblem: String?

    var body: some View {
        let lock = model.lock
        Section("Lock") {
            Toggle("Ask for Touch ID or my password to open LocalBook", isOn: Binding(get: { lock.enabled }, set: { lock.enabled = $0 }))
            if lock.enabled {
                Picker("Lock again after I have been away for", selection: Binding(get: { lock.lockAfterMinutes }, set: { lock.lockAfterMinutes = $0 })) {
                    Text("only at launch").tag(0)
                    ForEach([1, 5, 15, 30, 60], id: \.self) { Text("\($0) minute\($0 == 1 ? "" : "s")").tag($0) }
                }
                Button("Lock now") { lock.lockNow() }
            }
            Text(AppLock.biometryLine + ". The lock covers the window and the menu bar item; the agents keep working behind it, and the local API stays up for your own agents (their keys are the lock there).")
                .font(.callout).foregroundStyle(.secondary)
        }
        Section("Encryption at rest") {
            HStack {
                switch fileVault {
                case .on: Label("FileVault is on: this Mac's disk, and the library on it, are encrypted.", systemImage: "checkmark.shield.fill").foregroundStyle(Color.green)
                case .off: Label("FileVault is off. Turn it on in System Settings › Privacy & Security to encrypt this Mac's disk.", systemImage: "exclamationmark.shield").foregroundStyle(Color.orange)
                case .unknown(let why): Label("FileVault: \(why)", systemImage: "shield").foregroundStyle(.secondary)
                }
                Spacer()
                if case .off = fileVault { Button("Open System Settings") { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?FileVault")!) } }
            }
            Text("The library lives inside LocalBook's own container, which only LocalBook (and you, in the Finder) can open. A sandboxed app cannot mount its own encrypted disk image the way the old app did, so the disk's encryption is the layer here; backups are encrypted on their own, and the recovery phrase opens them anywhere.")
                .font(.callout).foregroundStyle(.secondary)
        }
        Section("While the window is closed") {
            Toggle("Keep LocalBook in the menu bar when its window is closed", isOn: $keepRunning)
            Text("Closing the window leaves LocalBook running, so the Collector, the Correspondent, the People profiler and the daily backup keep their schedules. The menu bar item shows what is running and opens the window again. Quit from there, or with ⌘Q, to stop everything.")
                .font(.callout).foregroundStyle(.secondary)
            Toggle("Open LocalBook when I log in", isOn: Binding(get: { loginOn }, set: { v in loginProblem = LoginItem.set(v); loginOn = LoginItem.isOn }))
            if LoginItem.needsApproval {
                HStack {
                    Text("macOS wants you to allow it under Login Items.").font(.callout).foregroundStyle(.orange)
                    Button("Open Login Items") { LoginItem.openSettings() }
                }
            }
            if let loginProblem { Text(loginProblem).font(.caption).foregroundStyle(.red) }
            Text("Together, these are how the agents run while you are not looking: LocalBook opens with your Mac and stays in the menu bar. Nothing runs when the Mac is asleep or you are logged out; a due schedule simply runs at the next chance.")
                .font(.callout).foregroundStyle(.secondary)
        }
        .task { fileVault = await FileVaultStatus.read(); loginOn = LoginItem.isOn }
    }
}

/// The menu bar item: what is running, the three "now" actions, open the window, quit.
struct BackgroundMenu: View {
    static let keepRunningKey = "LocalBook.keepRunningInMenuBar"
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        let collector = Agents.collector, mail = Agents.correspondent, people = Agents.people
        Group {
            if model.lock.locked { Text("Locked").foregroundStyle(.secondary) }
            if let s = mail.status { Text("Mail: \(s)") }
            else if let s = collector.status.values.first { Text("Collector: \(s)") }
            else if let s = people.status.values.first { Text("People: \(s)") }
            else { Text(model.readiness) }
            if mail.state.queue.count + collector.pendingTotal > 0 {
                Text("\(mail.state.queue.count + collector.pendingTotal) waiting for your approval").foregroundStyle(.secondary)
            }
            Divider()
            Button("Open LocalBook") { openWindow(id: "main"); NSApp.activate() }.keyboardShortcut("o")
            Divider()
            Button("Check Mail Now") { NotificationCenter.default.post(name: .correspondentAction, object: "check"); Task { await mail.pollAll(library: model.library) } }
                .disabled(mail.state.accounts.isEmpty || model.lock.locked)
            Button("Collect Now") { Task { for id in CollectorStore.notebooks() { if let nb = model.library.notebooks.first(where: { $0.id == id }) { await collector.collect(notebook: nb, library: model.library) } } } }
                .disabled(CollectorStore.notebooks().isEmpty || model.lock.locked)
            Button("Read Profiles Now") { Task { for id in PeopleStore.notebooks() { if let nb = model.library.notebooks.first(where: { $0.id == id }) { await people.collectAll(notebook: nb, library: model.library) } } } }
                .disabled(PeopleStore.notebooks().isEmpty || model.lock.locked)
            Divider()
            if model.lock.enabled, !model.lock.locked { Button("Lock LocalBook") { model.lock.lockNow() } }
            Button("Quit LocalBook") { NSApp.terminate(nil) }.keyboardShortcut("q")
        }
    }
}
