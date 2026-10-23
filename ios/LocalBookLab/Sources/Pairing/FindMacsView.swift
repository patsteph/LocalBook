import SwiftUI

struct FindMacsView: View {
    @State private var browser = MacBrowser()
    @State private var host = "192.168.86.66"
    @State private var port = "47603"
    @State private var pairingCode: String?
    @State private var pairStatus = "Not paired"
    @State private var busy = false
    @State private var paired = MacLink.paired

    var body: some View {
        List {
            Section {
                LabeledContent("Status", value: browser.state)
                if browser.macs.isEmpty && browser.browsing {
                    HStack { ProgressView(); Text("Searching the local network…") }
                }
            } footer: {
                Text("The first time, iOS asks to let LocalBook Lab find devices on your local network. "
                     + "Choose Allow — without it nothing can be found. The Mac must have sync turned on.")
            }
            Section("Macs found") {
                ForEach(browser.macs) { mac in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(mac.name).font(.body.weight(.medium))
                        Text(mac.deviceId.map { "Device id \($0)" } ?? "No device id in the advertisement")
                            .font(.footnote.monospaced()).foregroundStyle(.secondary)
                        HStack {
                            reachabilityLabel(mac.reachability)
                            Spacer()
                            Button("Check connection") { browser.checkReachable(mac) }
                                .buttonStyle(.bordered)
                        }
                        HStack(alignment: .top) {
                            tlsLabel(mac.tls)
                            Spacer()
                            Button("Test TLS") { browser.probeTLS(mac) }
                                .buttonStyle(.bordered)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
            Section {
                LabeledContent("This phone", value: PhoneIdentity.deviceId)
                if let p = paired {
                    LabeledContent(p.verified == true ? "Paired with" : "Asked to pair with", value: "\(p.name)")
                    if p.verified != true {
                        Text("Not verified yet: confirm the code on the Mac, then tap “2. Say hello”.")
                            .font(.footnote).foregroundStyle(.orange)
                    }
                }
                if let code = pairingCode {
                    VStack(alignment: .leading) {
                        Text("Code on this phone").font(.footnote).foregroundStyle(.secondary)
                        Text(code).font(.system(size: 40, weight: .bold, design: .monospaced))
                        Text("It must match the code on the Mac. Then tap “Codes match” on the Mac.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Text(pairStatus).font(.footnote).foregroundStyle(.secondary)
                Button("1. Pair with the Mac at the host below") { pair() }.disabled(busy)
                Button("2. Say hello (signed, mutual TLS)") { hello() }.disabled(busy || paired == nil)
                Button("Forget this Mac", role: .destructive) { MacLink.paired = nil; paired = nil; pairingCode = nil; pairStatus = "Not paired" }
                    .disabled(paired == nil)
            } header: {
                Text("Pair with a Mac")
            } footer: {
                Text("On the Mac: Settings › Sync › open pairing. The host below is the Mac’s address; the pairing ports are the Mac’s sync port + 2 and + 3.")
            }
            Section {
                TextField("Host", text: $host).textInputAutocapitalization(.never).autocorrectionDisabled()
                TextField("Port", text: $port).keyboardType(.numberPad)
                Button("Test TLS to this host and port") {
                    if let p = UInt16(port) { browser.probeTLS(host: host, port: p) }
                }
                tlsLabel(browser.manualResult)
                ForEach(browser.log, id: \.self) { Text($0).font(.caption2.monospaced()).foregroundStyle(.secondary) }
            } header: {
                Text("TLS check against any server")
            } footer: {
                Text("Port 47603 is the Mac’s phone link (P-256): it should hand over its certificate. The per-Mac “Test TLS” above hits the Mac-to-Mac port 47600, whose Ed25519 certificate iOS cannot use, so it is expected to fail.")
            }
        }
        .navigationTitle("Find Macs")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Button(browser.browsing ? "Restart" : "Search") { browser.start() }
        }
        .task { browser.start() }
        .onDisappear { browser.stop() }
    }

    private func pair() {
        busy = true; pairStatus = "Connecting to the pairing port…"; pairingCode = nil
        Task {
            defer { busy = false }
            do {
                let out = try await MacLink.requestPairing(host: host)
                pairingCode = out.code
                MacLink.paired = out.mac; paired = out.mac
                pairStatus = "Reached \(out.mac.name). Compare the code, then confirm on the Mac."
            } catch { pairStatus = "Pairing failed: \(error.localizedDescription)" }
        }
    }

    private func hello() {
        guard let mac = paired else { return }
        busy = true; pairStatus = "Calling the Mac…"
        Task {
            defer { busy = false }
            do {
                pairStatus = "Mac replied: \(try await MacLink.signedHello(mac))"
                paired = MacLink.paired
            } catch { pairStatus = "Hello failed: \(error.localizedDescription)" }
        }
    }

    @ViewBuilder private func tlsLabel(_ t: MacBrowser.TLSProbe) -> some View {
        switch t {
        case .notRun: Text("TLS not tested").foregroundStyle(.secondary)
        case .running: HStack { ProgressView(); Text("Handshaking…") }
        case .sawCertificate(let fp, let n):
            VStack(alignment: .leading, spacing: 2) {
                Label("Got the Mac's certificate (\(n) in chain)", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text(fp).font(.caption2.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
            }
        case .noCertificate(let why): Label(why, systemImage: "xmark.circle.fill").foregroundStyle(.red)
        }
    }

    @ViewBuilder private func reachabilityLabel(_ r: MacBrowser.Reachability) -> some View {
        switch r {
        case .unknown: Text("Not checked").foregroundStyle(.secondary)
        case .checking: HStack { ProgressView(); Text("Connecting…") }
        case .reachable(let ms): Label("Reachable (\(ms) ms)", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        case .unreachable(let why): Label(why, systemImage: "xmark.circle.fill").foregroundStyle(.red)
        }
    }
}
