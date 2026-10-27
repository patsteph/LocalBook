import SwiftUI

/// Welcome → pick your Mac → compare the code → first sync → ready.
struct OnboardingView: View {
    let library: LibraryModel
    let onDone: () -> Void

    enum Step: Equatable {
        case welcome
        case findMac
        case confirm(code: String, macName: String)
        case syncing
        case failed(String)
    }

    @State private var step: Step = .welcome
    @State private var browser = MacBrowser()
    @State private var manualHost = ""
    @State private var working = false
    @State private var showHelp = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                switch step {
                case .welcome: welcome
                case .findMac: findMac
                case .confirm(let code, let name): confirm(code: code, macName: name)
                case .syncing: syncing
                case .failed(let why): failed(why)
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .navigationTitle("LocalBook")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Text(stepLabel).font(.footnote).foregroundStyle(.secondary) } }
            .sheet(isPresented: $showHelp) { helpSheet }
        }
    }

    private var stepLabel: String {
        switch step {
        case .welcome: "Step 1 of 4"
        case .findMac: "Step 2 of 4"
        case .confirm: "Step 3 of 4"
        case .syncing: "Step 4 of 4"
        case .failed: ""
        }
    }

    private var helpSheet: some View {
        NavigationStack {
            List {
                Text("Both devices must be on the same Wi-Fi.")
                Text("On the Mac: LocalBook › Settings › Sync, turn Sync on, then tap “Let another device pair with this one”.")
                Text("On this phone: Settings › Privacy & Security › Local Network, and make sure LocalBook is on.")
                Text("Still stuck? Enter the Mac's address instead; it is shown next to the pairing window on the Mac.")
            }
            .navigationTitle("Can't find your Mac?").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { showHelp = false } } }
        }
        .presentationDetents([.medium])
    }

    // MARK: steps

    private var welcome: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "books.vertical.fill").font(.system(size: 64)).foregroundStyle(.tint)
            Text("Your notebooks, on your phone").font(.title2.bold()).multilineTextAlignment(.center)
            Text("LocalBook copies your library from your Mac over your own network, then searches it and answers questions right here. Nothing goes to the cloud, and your questions never leave the phone.")
                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Spacer()
            Text("Next, iOS asks to find devices on your local network. Tap Allow; that is how this phone talks to your Mac.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Text("On your Mac, open LocalBook › Settings › Sync, turn Sync on, and tap “Let another device pair with this one”.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button { step = .findMac; browser.start() } label: { Text("Find my Mac").frame(maxWidth: .infinity) }
                .buttonStyle(.borderedProminent).controlSize(.large)
        }
    }

    private var findMac: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Choose your Mac").font(.title3.bold())
            if browser.macs.isEmpty {
                HStack { ProgressView(); Text("Looking on this network…").foregroundStyle(.secondary) }
                Text("If iOS asks to find devices on your local network, choose Allow. The Mac needs Sync turned on and its pairing window open.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(browser.macs) { mac in
                Button {
                    Task { await pair(with: mac) }
                } label: {
                    HStack {
                        Image(systemName: "laptopcomputer")
                        Text(mac.name)
                        Spacer()
                        if working { ProgressView() } else { Image(systemName: "chevron.right").foregroundStyle(.secondary) }
                    }
                    .padding().background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain).disabled(working)
            }
            Button("Can't find your Mac?") { showHelp = true }.font(.footnote)
            DisclosureGroup("Enter the Mac's address instead") {
                HStack {
                    TextField("e.g. 192.168.1.20", text: $manualHost)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.numbersAndPunctuation)
                    Button("Connect") { Task { await pair(host: manualHost.trimmingCharacters(in: .whitespaces)) } }
                        .disabled(working || manualHost.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .onDisappear { browser.stop() }
    }

    private func confirm(code: String, macName: String) -> some View {
        VStack(spacing: 20) {
            Spacer()
            Text("Does \(macName) show this code?").font(.title3.bold()).multilineTextAlignment(.center)
            Text(code).font(.system(size: 52, weight: .bold, design: .monospaced))
            Text("If it matches, tap “Codes match” on the Mac. This phone then finishes by itself.")
                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
            ProgressView()
            Spacer()
            Button("Cancel", role: .destructive) { MacLink.paired = nil; step = .welcome }
        }
        .task { await waitForMac() }
    }

    private var syncing: some View {
        VStack(spacing: 20) {
            Spacer()
            ProgressView().controlSize(.large)
            Text("Copying your library").font(.title3.bold())
            if case .syncing(let line) = library.syncState { Text(line).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center) }
            if case .failed(let why) = library.syncState { Text(why).font(.footnote).foregroundStyle(.red) }
            Text("The first time takes a minute or two. Keep the app open.").font(.footnote).foregroundStyle(.secondary)
            Spacer()
        }
        .task { await firstSync() }
    }

    private func failed(_ why: String) -> some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "exclamationmark.triangle.fill").font(.largeTitle).foregroundStyle(.orange)
            Text(why).multilineTextAlignment(.center)
            Button("Try again") { step = .findMac; browser.start() }.buttonStyle(.borderedProminent)
            Spacer()
        }
    }

    // MARK: actions

    private func pair(with mac: MacBrowser.Mac) async {
        working = true; defer { working = false }
        guard let host = await browser.resolveHost(mac) else { step = .failed("Couldn't reach \(mac.name). Check that it is on the same Wi-Fi."); return }
        await pair(host: host)
    }

    private func pair(host: String) async {
        working = true; defer { working = false }
        do {
            let out = try await MacLink.requestPairing(host: host)
            MacLink.paired = out.mac
            step = .confirm(code: out.code, macName: out.mac.name)
        } catch {
            step = .failed("Pairing didn't start: \(error.localizedDescription)\n\nOn the Mac, tap “Let another device pair with this one” and try again.")
        }
    }

    /// The Mac side confirms the code; until then the signed hello is refused. Ask every two seconds.
    private func waitForMac() async {
        guard let mac = MacLink.paired else { return }
        for _ in 0..<90 {
            if Task.isCancelled { return }
            if (try? await MacLink.signedHello(mac)) != nil { step = .syncing; return }
            try? await Task.sleep(for: .seconds(2))
        }
        step = .failed("The Mac didn't confirm the code in time. Start again from the Mac's pairing window.")
    }

    private func firstSync() async {
        await library.open()
        // Ask opens as soon as the first notebook has copied; the rest keeps copying in the background.
        let copying = Task { await library.sync() }
        while !copying.isCancelled {
            if !library.notebooks.isEmpty { break }
            if case .failed = library.syncState { break }
            if case .idle = library.syncState, library.lastSummary != nil { break }
            try? await Task.sleep(for: .milliseconds(300))
        }
        if case .failed = library.syncState, library.notebooks.isEmpty {
            step = .failed("Syncing didn't finish. Details: \(syncError)"); return
        }
        await library.prepareSearch()
        onDone()
    }

    private var syncError: String {
        if case .failed(let why) = library.syncState { return why }
        return "unknown"
    }
}
