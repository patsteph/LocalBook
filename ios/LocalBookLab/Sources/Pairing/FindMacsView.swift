import SwiftUI

struct FindMacsView: View {
    @State private var browser = MacBrowser()

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
                    }
                    .padding(.vertical, 2)
                }
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

    @ViewBuilder private func reachabilityLabel(_ r: MacBrowser.Reachability) -> some View {
        switch r {
        case .unknown: Text("Not checked").foregroundStyle(.secondary)
        case .checking: HStack { ProgressView(); Text("Connecting…") }
        case .reachable(let ms): Label("Reachable (\(ms) ms)", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        case .unreachable(let why): Label(why, systemImage: "xmark.circle.fill").foregroundStyle(.red)
        }
    }
}
