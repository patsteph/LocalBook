import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Pair this Mac with the Mac that holds the library (the old app's, or another Swift Mac), over the same link the
/// phone uses: find it on the network, compare the six-digit code, copy the first notebook. The phone's
/// `OnboardingView`, laid out as a sheet.
struct PairingSheet: View {
    let model: AppModel
    @Environment(\.dismiss) private var dismiss

    enum Step: Equatable {
        case findMac
        case confirm(code: String, macName: String)
        case syncing
        case failed(String)
    }

    @State private var step: Step = .findMac
    @State private var browser = MacBrowser()
    @State private var manualHost = ""
    @State private var working = false

    private var library: LibraryModel { model.library }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Group {
                switch step {
                case .findMac: findMac
                case .confirm(let code, let name): confirm(code: code, macName: name)
                case .syncing: syncing
                case .failed(let why): failed(why)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(24)
            Divider()
            HStack {
                if case .findMac = step {
                    Button("Moving from the LocalBook on this Mac? Move my library here…") { model.showPairing = false; model.showImport = true }
                        .buttonStyle(.link).font(.footnote)
                }
                Spacer()
                Button(step == .syncing ? "Close" : "Cancel") { cancel() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(16)
        }
        .frame(width: 520, height: 440)
        .onAppear { browser.start() }
        .onDisappear { browser.stop() }
    }

    // MARK: steps

    private var findMac: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Choose the Mac that holds your notebooks").font(.title2.bold())
            Text("On that Mac, open LocalBook › Settings › Sync and click “Let a device pair with this one” (in the old app: turn Sync on, then “Let another device pair with this one”). Your library is copied over your own network; nothing goes to the cloud.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if browser.macs.isEmpty {
                HStack { ProgressView().controlSize(.small); Text("Looking on this network…").foregroundStyle(.secondary) }
                    .padding(.top, 4)
            }
            VStack(spacing: 6) {
                ForEach(browser.macs) { mac in
                    Button {
                        Task { await pair(with: mac) }
                    } label: {
                        HStack {
                            Image(systemName: "laptopcomputer")
                            Text(mac.name)
                            Spacer()
                            if working { ProgressView().controlSize(.small) } else { Image(systemName: "chevron.right").foregroundStyle(.secondary) }
                        }
                        .padding(10).background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain).disabled(working)
                }
            }
            DisclosureGroup("Enter the Mac's address instead") {
                HStack {
                    TextField("e.g. 192.168.1.20", text: $manualHost).textFieldStyle(.roundedBorder)
                        .onSubmit { Task { await pair(host: manualHost.trimmingCharacters(in: .whitespaces)) } }
                    Button("Connect") { Task { await pair(host: manualHost.trimmingCharacters(in: .whitespaces)) } }
                        .disabled(working || manualHost.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                .padding(.top, 6)
            }
            .font(.callout)
            Spacer()
        }
    }

    private func confirm(code: String, macName: String) -> some View {
        VStack(spacing: 18) {
            Spacer()
            Text("Does \(macName) show this code?").font(.title2.bold()).multilineTextAlignment(.center)
            Text(code).font(.system(size: 56, weight: .bold, design: .monospaced))
            Text("If it matches, click “Codes match” on \(macName). This Mac then finishes by itself.")
                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
            ProgressView().controlSize(.small)
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .task { await waitForMac() }
    }

    private var syncing: some View {
        VStack(spacing: 18) {
            Spacer()
            ProgressView().controlSize(.large)
            Text("Copying your library").font(.title2.bold())
            if case .syncing(let line) = library.syncState { Text(line).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center) }
            if case .failed(let why) = library.syncState { Text(why).font(.footnote).foregroundStyle(.red) }
            Text("The first time takes a minute or two. This sheet closes as soon as the first notebook is here; the rest keeps copying.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .task { await firstSync() }
    }

    private func failed(_ why: String) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "exclamationmark.triangle.fill").font(.largeTitle).foregroundStyle(.orange)
            Text(why).multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
            Button("Try again") { step = .findMac; browser.start() }.buttonStyle(.borderedProminent)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: actions

    private func cancel() {
        if case .syncing = step { model.pairingDone() } else if case .confirm = step { MacLink.paired = nil }
        dismiss()
    }

    private func pair(with mac: MacBrowser.Mac) async {
        working = true; defer { working = false }
        guard let host = await browser.resolveHost(mac) else { step = .failed("Couldn't reach \(mac.name). Check that both Macs are on the same network."); return }
        await pair(host: host)
    }

    private func pair(host: String) async {
        working = true; defer { working = false }
        do {
            let out = try await MacLink.requestPairing(host: host)
            MacLink.paired = out.mac
            step = .confirm(code: out.code, macName: out.mac.name)
        } catch {
            step = .failed("Pairing didn't start: \(error.localizedDescription)\n\nOn the other Mac, click “Let another device pair with this one” and try again.")
        }
    }

    /// The other Mac confirms the code; until then the signed hello is refused. Ask every two seconds.
    private func waitForMac() async {
        guard let mac = MacLink.paired else { return }
        for _ in 0..<90 {
            if Task.isCancelled { return }
            if (try? await MacLink.signedHello(mac)) != nil { step = .syncing; return }
            try? await Task.sleep(for: .seconds(2))
        }
        step = .failed("The other Mac didn't confirm the code in time. Start again from its pairing window.")
    }

    private func firstSync() async {
        await library.open()
        let copying = Task { await library.sync() }
        while !copying.isCancelled {
            if !library.notebooks.isEmpty { break }
            if case .failed = library.syncState { break }
            if case .idle = library.syncState, library.lastSummary != nil { break }
            try? await Task.sleep(for: .milliseconds(300))
        }
        if case .failed(let why) = library.syncState, library.notebooks.isEmpty {
            step = .failed("Syncing didn't finish. Details: \(why)"); return
        }
        model.pairingDone()
        dismiss()
    }
}
