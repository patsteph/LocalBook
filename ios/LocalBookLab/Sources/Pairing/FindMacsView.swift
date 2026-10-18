import SwiftUI

struct FindMacsView: View {
    @State private var browser = MacBrowser()
    @State private var host = "192.168.86.66"
    @State private var port = "47651"

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
                Text("For diagnosing which certificate types this iPhone's TLS accepts.")
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
