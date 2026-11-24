import Foundation
import LocalBookCore
import LocalBookEngines
import Observation

/// This Mac as the host of the phone link, for the Settings › Sync pane: the listener's state, the pairing window,
/// requests waiting for the code to be compared, the paired devices, and which notebooks stay on this Mac.
/// Mirrors the package's `PhoneHost` actor, which does the work.
@Observable @MainActor
final class PhoneHostController {
    enum Status: Equatable { case off, listening, failed(String) }

    private(set) var status: Status = .off
    private(set) var pairingOpenUntil: Date?
    private(set) var requests: [PairingRequest] = []
    private(set) var phones: [PairedPhone] = []
    private(set) var keptLocal: Set<String> = []
    /// What the person can type into a phone that does not find this Mac by name.
    private(set) var addresses: [String] = []
    var problem: String?

    private var host: PhoneHost?
    private let library: LibraryModel

    init(library: LibraryModel) { self.library = library }

    var isListening: Bool { status == .listening }
    var pairingOpen: Bool { pairingOpenUntil.map { $0 > Date() } ?? false }

    /// Opens the sync port as soon as the library is open: the iPhone can always find this Mac (offline-first: nothing
    /// here waits on anything remote). A port already taken by the LocalBook of old on this Mac is reported, not fatal.
    func start() async {
        guard host == nil, let store = library.store else { return }
        let h = PhoneHost(store: store, delegate: PhoneHostBridge(library: library))
        host = h
        await h.setOnChange { [weak self] in Task { @MainActor in await self?.refresh() } }
        do { try await h.start() } catch { problem = error.localizedDescription }
        await refresh()
    }

    func stop() async {
        await host?.stop()
        await refresh()
    }

    func openPairing() async {
        guard let host else { await start(); if self.host != nil { await openPairing() }; return }
        do { _ = try await host.openPairing(); problem = nil } catch { problem = error.localizedDescription }
        addresses = LocalAddresses.ipv4()
        await refresh()
    }

    func closePairing() async { await host?.closePairing(); await refresh() }
    func confirm(_ request: PairingRequest) async { _ = await host?.confirm(request.id); await refresh() }
    func reject(_ request: PairingRequest) async { await host?.reject(request.id); await refresh() }
    func forget(_ phone: PairedPhone) async { await host?.forget(phone.deviceId); await refresh() }

    /// A notebook is either synced to every paired device or kept on this Mac.
    func setSynced(_ notebook: String, _ synced: Bool) async {
        if let host { await host.setKeptLocal(notebook, !synced) }
        else if let store = library.store { try? await store.setKeptLocal(notebook, !synced) }
        await refresh()
    }

    func refresh() async {
        if let host {
            switch await host.state {
            case .off: status = .off
            case .listening: status = .listening
            case .failed(let why): status = .failed(why)
            }
            pairingOpenUntil = await host.pairingOpenUntil
            requests = await host.requests
            phones = await host.pairedPhones
        } else {
            phones = PhoneHostStore.shared.phones()
        }
        if let store = library.store { keptLocal = await store.keptLocal() }
    }
}
