import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// The user's Mail folder, granted once through the open panel and kept as a security-scoped bookmark. Reading
/// `~/Library/Mail` also needs Full Disk Access for this app in System Settings; the pane links there.
enum MailFolderAccess {
    static let bookmarkKey = "LocalBook.mailFolderBookmark"

    static func restore() -> URL? {
        guard let data = UserDefaults.standard.data(forKey: bookmarkKey) else { return nil }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: data, options: .withSecurityScope, relativeTo: nil, bookmarkDataIsStale: &stale) else { return nil }
        _ = url.startAccessingSecurityScopedResource()   // held for the app's life; the folder is read on every poll
        return url
    }

    @MainActor static func choose() -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.canCreateDirectories = false
        panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Mail")
        panel.prompt = "Use this folder"; panel.message = "Pick your Mail folder (Library › Mail). LocalBook reads it in place and never writes to it."
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        if let bookmark = try? url.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil) {
            UserDefaults.standard.set(bookmark, forKey: bookmarkKey)
        }
        _ = url.startAccessingSecurityScopedResource()
        return url
    }

    static func openFullDiskAccessSettings() {
        if let u = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") { NSWorkspace.shared.open(u) }
    }
}

/// The Correspondent: connected inboxes (Mail on this Mac or IMAP), what is waiting for a notebook, the weekly
/// journal, and the history of its runs.
struct CorrespondentPane: View {
    @Environment(AppModel.self) private var model
    @State private var tab: Tab = .queue
    @State private var editing: MailAccount?
    @State private var adding = false
    @State private var picks: [String: String] = [:]

    enum Tab: String, CaseIterable { case queue = "Waiting", accounts = "Inboxes", journal = "Journal", history = "History" }

    private var engine: CorrespondentEngine { Agents.correspondent }
    private var library: LibraryModel { model.library }
    private var busy: Bool { !engine.running.isEmpty }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            Group {
                switch tab {
                case .queue: queue
                case .accounts: accounts
                case .journal: journal
                case .history: history
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onChange(of: model.correspondentRequest, initial: true) { _, r in
            guard let r else { return }
            model.correspondentRequest = nil
            if r == .checkMail { Task { await engine.pollAll(library: library) } }
        }
        .sheet(isPresented: $adding) { AccountSheet(account: MailAccount(email: "", path: .mailApp), isNew: true) }
        .sheet(item: $editing) { a in AccountSheet(account: a, isNew: false) }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "envelope").foregroundStyle(.indigo)
            VStack(alignment: .leading, spacing: 0) {
                Text("The Correspondent").font(.headline)
                Text(engine.state.accounts.isEmpty ? "Connect an inbox: newsletters land in the right notebook, forwards go where you point them, personal mail is never read."
                     : "\(engine.state.accounts.count) inbox\(engine.state.accounts.count == 1 ? "" : "es") · checks every \(engine.state.pollHours) hours while the app is open")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if busy { ProgressView().controlSize(.small); Text(engine.status ?? "Working…").font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            else if let e = engine.error { Text(e).font(.caption).foregroundStyle(.red).lineLimit(1) }
            Picker("", selection: $tab) { ForEach(Tab.allCases, id: \.self) { t in Text(t == .queue && !engine.state.queue.isEmpty ? "Waiting · \(engine.state.queue.count)" : t.rawValue).tag(t) } }
                .pickerStyle(.segmented).frame(width: 380)
            Button { Task { await engine.pollAll(library: library) } } label: { Label("Check mail now", systemImage: "arrow.down.circle") }
                .disabled(busy || engine.state.accounts.allSatisfy { !$0.enabled || !$0.isConfigured })
        }
        .controlSize(.small)
        .padding(.horizontal, 12).padding(.vertical, 8)
    }

    // MARK: waiting

    private var queue: some View {
        let items = engine.queue
        return ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if items.isEmpty {
                    Text("Nothing waiting. Newsletters the Correspondent cannot place with confidence, and forwards without a #notebook tag, land here for you to pick a notebook.")
                        .foregroundStyle(.secondary).padding(.top, 20)
                } else {
                    HStack { Text("\(items.count) waiting for you").font(.headline); Spacer()
                        Button("Approve all into their best match") { Task { await engine.approveAll(notebookId: nil, library: library) } }.disabled(busy || items.allSatisfy { $0.topCandidate == nil }) }
                    ForEach(items) { item in queueRow(item) }
                }
            }
            .padding(16)
        }
    }

    private func queueRow(_ item: MailQueueItem) -> some View {
        let chosen = Binding<String>(get: { picks[item.id] ?? item.topCandidate?.notebookId ?? "" }, set: { picks[item.id] = $0 })
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(item.kind == .forward ? "Forward" : "Newsletter").font(.caption2.weight(.semibold)).padding(.horizontal, 6).padding(.vertical, 2)
                            .background(item.kind == .forward ? Color.orange.opacity(0.2) : Color.indigo.opacity(0.15), in: Capsule())
                        Text(item.subject).font(.headline).lineLimit(2)
                    }
                    Text("\(item.sender) · \(item.createdAt.formatted(date: .abbreviated, time: .shortened))").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if let top = item.topCandidate {
                    VStack(alignment: .trailing) {
                        Text("\(Int(top.confidence * 100)) %").font(.title3.monospacedDigit().weight(.semibold)).foregroundStyle(top.confidence >= 0.6 ? .orange : .secondary)
                        Text("best match: \(top.notebookName)").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if !item.summary.isEmpty { Text(item.summary).font(.callout).lineLimit(3).foregroundStyle(.primary.opacity(0.85)) }
            if !item.topicTags.isEmpty { Text(item.topicTags.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary) }
            Text(item.decisionReason).font(.caption2).foregroundStyle(.tertiary)
            HStack {
                Picker("Notebook", selection: chosen) {
                    Text("Choose…").tag("")
                    ForEach(library.notebooks) { nb in Text(nb.title).tag(nb.id) }
                }.frame(maxWidth: 320)
                Button("Approve") { Task { _ = await engine.approve(item.id, notebookId: chosen.wrappedValue.isEmpty ? nil : chosen.wrappedValue, library: library) } }
                    .disabled(busy || (chosen.wrappedValue.isEmpty && item.topCandidate == nil))
                    .help("Ingest into the chosen notebook; the Correspondent learns this sender's home from it")
                Button("Dismiss") { engine.dismiss(item.id) }
                Spacer()
            }.controlSize(.small)
        }
        .padding(12)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
    }

    // MARK: inboxes

    private var accounts: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Connected inboxes").font(.headline)
                    Spacer()
                    Button { adding = true } label: { Label("Add an inbox", systemImage: "plus") }
                }
                if engine.state.accounts.isEmpty {
                    Text("No inboxes connected yet. Mail on this Mac needs your Mail folder and Full Disk Access; an IMAP account needs an app password.").foregroundStyle(.secondary)
                }
                ForEach(engine.state.accounts) { a in accountRow(a) }
                Divider().padding(.vertical, 4)
                Text("How it decides").font(.headline)
                Text("Each new message is read once. A forward (Fwd: subject, or a forwarded-message block) goes to the notebook named with #its-slug in the subject, or to the notebook whose digest matches it, or waits here; the sender gets a short confirmation. Anything else the answer model sorts into newsletter, personal or transactional: newsletters are placed by the same digest match (\(Int(MailRouter.threshold * 100)) % or better to go on their own, otherwise they wait here), personal mail is moved to \(engine.state.personalFolder) and never read, receipts and alerts are marked read and left alone. What you approve here teaches the Correspondent where that sender belongs. Nothing leaves this Mac to decide any of it.")
                    .font(.callout).foregroundStyle(.secondary)
                Stepper("Check inboxes every \(engine.state.pollHours) hours", value: Binding(get: { engine.state.pollHours }, set: { v in var s = engine.state; s.pollHours = max(1, min(48, v)); engine.replaceState(s) }), in: 1...48)
                    .frame(maxWidth: 320)
            }
            .padding(16)
        }
    }

    private func accountRow(_ a: MailAccount) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: a.path == .mailApp ? "macwindow" : "server.rack").foregroundStyle(a.enabled ? .indigo : .secondary).frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(a.email.isEmpty ? a.mailboxURL : a.email).font(.headline)
                Text(a.path.label + (a.path == .imap ? " · \(a.imapHost)" : "") + (a.canSend ? "" : " · cannot send") + (a.enabled ? "" : " · paused")).font(.caption).foregroundStyle(.secondary)
                if let p = a.lastPolledAt { Text("Last checked \(p.formatted(date: .abbreviated, time: .shortened))" + (a.lastError.map { " · \($0)" } ?? "")).font(.caption).foregroundStyle(a.lastError == nil ? .secondary : .red).lineLimit(2) }
                else { Text("Not checked yet").font(.caption).foregroundStyle(.secondary) }
            }
            Spacer()
            Toggle("", isOn: Binding(get: { a.enabled }, set: { v in var b = a; b.enabled = v; engine.update(b) })).toggleStyle(.switch).controlSize(.mini)
            Button("Check now") { Task { await engine.poll(a, library: library) } }.disabled(busy || !a.isConfigured)
            Button("Edit…") { editing = a }
            Button(role: .destructive) { engine.remove(a.id) } label: { Image(systemName: "trash") }.help("Remove inbox")
        }
        .controlSize(.small)
        .padding(12)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
    }

    // MARK: journal

    private var journal: some View {
        let preview = engine.journalPreview(library: library)
        return ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Weekly journal").font(.headline)
                Text("Once a week, in the morning, each inbox that can send gets a short letter about what came through: new sources, the busiest notebooks, the questions you asked. It is written from the app's own event log, so an empty week sends nothing.").font(.callout).foregroundStyle(.secondary)
                ForEach(engine.state.accounts) { a in
                    HStack {
                        Toggle(a.email, isOn: Binding(get: { a.weeklyJournalEnabled }, set: { v in var b = a; b.weeklyJournalEnabled = v; engine.update(b) }))
                        Spacer()
                        if let j = a.lastJournalAt { Text("last sent \(j.formatted(date: .abbreviated, time: .omitted))").font(.caption).foregroundStyle(.secondary) }
                        Button("Send now") { Task { await engine.sendJournal(a, library: library) } }.disabled(busy || !a.canSend || preview == nil)
                    }.controlSize(.small)
                }
                Divider()
                Text("This week's letter").font(.subheadline.weight(.semibold))
                if let preview {
                    Text(preview.text).font(.callout).textSelection(.enabled).padding(12).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
                } else {
                    Text("Nothing happened this week yet, so there is nothing to send.").foregroundStyle(.secondary)
                }
            }
            .padding(16)
        }
    }

    // MARK: history

    private var history: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                if engine.runs.isEmpty { Text("No runs yet.").foregroundStyle(.secondary).padding(.top, 20) }
                ForEach(engine.runs) { r in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(engine.state.account(r.accountId)?.email ?? r.accountId).font(.subheadline.weight(.semibold))
                            Text(r.startedAt.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                            Text(r.trigger).font(.caption2).foregroundStyle(.tertiary)
                            Spacer()
                        }
                        Text(r.line).font(.callout)
                        ForEach(r.problems, id: \.self) { p in Text(p).font(.caption).foregroundStyle(.red) }
                    }
                    .padding(10).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
                }
            }
            .padding(16)
        }
    }
}

/// Adding or editing one inbox.
struct AccountSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State var account: MailAccount
    let isNew: Bool
    @State private var password = ""
    @State private var testLine: String?
    @State private var testing = false
    @State private var inboxes: [MailAppStore.Mailbox]?

    private var engine: CorrespondentEngine { Agents.correspondent }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(isNew ? "Add an inbox" : "Edit inbox").font(.title3.weight(.semibold))
            Picker("Where it lives", selection: $account.path) { ForEach(MailAccount.Path.allCases, id: \.self) { p in Text(p.label).tag(p) } }.pickerStyle(.segmented)
            Form {
                if account.path == .mailApp { mailAppFields } else { imapFields }
                Section("Behaviour") {
                    Toggle("Reply to confirm a forward landed", isOn: $account.sendConfirmations)
                    Toggle("Send me a weekly journal of what I learned", isOn: $account.weeklyJournalEnabled)
                    Picker("Forwards with no match go to", selection: Binding(get: { account.defaultForwardNotebookId ?? "" }, set: { account.defaultForwardNotebookId = $0.isEmpty ? nil : $0 })) {
                        Text("the waiting list").tag("")
                        ForEach(model.library.notebooks) { nb in Text(nb.title).tag(nb.id) }
                    }
                }
            }
            .formStyle(.grouped)
            if let testLine { Text(testLine).font(.caption).foregroundStyle(.secondary) }
            HStack {
                Button("Test") { Task { testing = true; testLine = await engine.test(account, password: password.isEmpty ? nil : password); testing = false } }.disabled(testing || !account.isConfigured)
                Spacer()
                Button("Cancel") { dismiss() }
                Button(isNew ? "Add" : "Save") {
                    if account.path == .mailApp, account.email.isEmpty, let u = URL(string: account.mailboxURL) { account.email = u.user(percentEncoded: false) ?? "" }
                    let fresh = isNew && account.path == .mailApp && account.lastUID == 0
                    engine.add(account, password: password.isEmpty ? nil : password)
                    if fresh { engine.startFromNow(account) }
                    dismiss()
                }.keyboardShortcut(.defaultAction).disabled(!account.isConfigured || (account.path == .imap && isNew && password.isEmpty))
            }
        }
        .padding(20)
        .frame(width: 560)
        .onAppear { inboxes = engine.mailAppInboxes() }
    }

    @ViewBuilder private var mailAppFields: some View {
        Section("Mail on this Mac") {
            HStack {
                Text(engine.mailFolder.map { "Mail folder: \($0.path)" } ?? "Mail folder not chosen yet").font(.caption).lineLimit(1).truncationMode(.middle)
                Spacer()
                Button("Choose Mail folder…") { if let u = MailFolderAccess.choose() { engine.mailFolder = u; inboxes = engine.mailAppInboxes() } }
            }
            if engine.mailFolder != nil, inboxes == nil {
                Text("The folder is chosen but Mail's index could not be read. Give LocalBook Full Disk Access, then come back.").font(.caption).foregroundStyle(.orange)
                Button("Open Full Disk Access settings") { MailFolderAccess.openFullDiskAccessSettings() }
            }
            if let inboxes {
                Picker("Inbox", selection: $account.mailboxURL) {
                    Text("Choose…").tag("")
                    ForEach(inboxes) { b in Text(b.label).tag(b.url) }
                }
                if inboxes.isEmpty { Text("No inboxes in Mail's index.").font(.caption).foregroundStyle(.secondary) }
            }
            TextField("Your address (for the journal and replies)", text: $account.email)
            Text("LocalBook reads Mail's store in place and asks Mail to move, mark or send. macOS asks once to allow that.").font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var imapFields: some View {
        Section("IMAP account") {
            TextField("Email", text: $account.email, prompt: Text("you@example.com"))
            TextField("IMAP host", text: $account.imapHost, prompt: Text("imap.example.com"))
            HStack { TextField("Port", value: $account.imapPort, format: .number).frame(width: 90); Toggle("TLS", isOn: $account.useSSL) }
            TextField("User name (if not the address)", text: $account.imapUser)
            SecureField(isNew ? "App password" : "App password (leave empty to keep)", text: $password, prompt: Text("provider-issued app password"))
        }
        Section("Outbound mail (confirmations and the journal)") {
            TextField("SMTP host", text: $account.smtpHost, prompt: Text("smtp.example.com"))
            HStack { TextField("SMTP port", value: $account.smtpPort, format: .number).frame(width: 90); Toggle("TLS", isOn: $account.smtpUseTLS) }
            Text("Port 465 with TLS. The IMAP sign-in is reused.").font(.caption).foregroundStyle(.secondary)
        }
    }
}
