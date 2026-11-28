import Foundation
import LocalBookCore
import Observation

/// The Correspondent: reads each connected inbox, sorts what arrived (newsletters into the right notebook, forwards
/// where the user pointed them, personal mail moved aside and never read, receipts left alone), asks before it is
/// unsure, confirms forwards by reply, and mails a weekly journal. Two inbox paths behind one `Mailbox`: Mail.app's
/// store on this Mac, and IMAP for the rest. Everything decides locally; the only remote parties are the user's own
/// mail servers.
@MainActor @Observable
public final class CorrespondentEngine {
    public private(set) var state: CorrespondentState
    public private(set) var running: Set<String> = []
    public var status: String?
    public var error: String?
    /// The user's Mail folder (`~/Library/Mail`), resolved from its bookmark by the app with security scope held.
    public var mailFolder: URL?
    private var timer: Timer?
    private var emlxCache: [String: [Int: URL]] = [:]

    public init() { state = CorrespondentStore.load() }

    private func save() { CorrespondentStore.save(state) }

    // MARK: accounts

    public func add(_ account: MailAccount, password: String?) {
        var a = account
        if a.path == .imap, a.smtpHost.isEmpty, !a.imapHost.isEmpty {
            // Most providers pair imap.x with smtp.x.
            a.smtpHost = a.imapHost.replacingOccurrences(of: "imap.", with: "smtp.").replacingOccurrences(of: "imap-mail.", with: "smtp-mail.")
            if a.smtpHost == a.imapHost { a.smtpHost = "" }
        }
        state.update(a)
        if let password, !password.isEmpty { MailSecrets.set(password, for: a.id) }
        save()
    }

    public func update(_ account: MailAccount) { state.update(account); save() }

    /// For the settings the pane edits in place (the poll interval).
    public func replaceState(_ s: CorrespondentState) { state = s; save() }

    public func remove(_ accountId: String) {
        state.accounts.removeAll { $0.id == accountId }
        state.queue.removeAll { $0.accountId == accountId }
        MailSecrets.remove(for: accountId)
        save()
    }

    public var queue: [MailQueueItem] { state.queue.sorted { $0.createdAt > $1.createdAt } }
    public var runs: [MailRun] { state.runs.reversed() }

    /// A Mail.app account starts from now: the rows already in the index are the user's past, not new mail.
    public func startFromNow(_ account: MailAccount) {
        guard account.path == .mailApp, let folder = mailFolder, let index = MailAppStore.indexURL(in: folder) else { return }
        var a = account
        a.lastUID = (try? MailAppStore.latestRowId(index: index, mailboxURL: account.mailboxURL)) ?? 0
        update(a)
    }

    /// The inboxes Mail knows, for the account picker; nil when the folder is not readable (no grant yet).
    public func mailAppInboxes() -> [MailAppStore.Mailbox]? {
        guard let folder = mailFolder, let index = MailAppStore.indexURL(in: folder) else { return nil }
        return try? MailAppStore.inboxes(index: index)
    }

    /// Tries the sign-in and INBOX without reading anything: the "Test" button.
    public func test(_ account: MailAccount, password: String?) async -> String {
        switch account.path {
        case .mailApp:
            guard let folder = mailFolder else { return "Choose your Mail folder first" }
            guard let index = MailAppStore.indexURL(in: folder) else { return "No Envelope Index under \(folder.lastPathComponent): pick ~/Library/Mail" }
            do { let n = try MailAppStore.latestRowId(index: index, mailboxURL: account.mailboxURL); return "Mail's index opens; the inbox is at message \(n)" }
            catch { return error.localizedDescription }
        case .imap:
            guard let pw = password ?? MailSecrets.password(for: account.id) else { return "No password saved" }
            let client = IMAPClient(host: account.imapHost, port: account.imapPort, tls: account.useSSL)
            do {
                try await client.connect(user: account.imapUser.isEmpty ? account.email : account.imapUser, password: pw)
                try await client.select()
                let unseen = try await client.unseenUIDs(after: account.lastUID)
                await client.logout()
                return "Signed in; \(unseen.count) unread message\(unseen.count == 1 ? "" : "s") waiting"
            } catch { await client.logout(); return error.localizedDescription }
        }
    }

    // MARK: schedule

    /// Every half hour: poll the accounts whose interval has passed, send journals that are due. Only while the app
    /// is open; the launch agent (flight 8) covers the rest.
    public func startSchedule(library: LibraryModel) {
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 1_800, repeats: true) { [weak self] _ in Task { @MainActor in await self?.runDue(library: library) } }
        Task { @MainActor in try? await Task.sleep(for: .seconds(90)); await runDue(library: library) }
    }

    public func runDue(library: LibraryModel) async {
        for a in state.accounts where a.enabled && a.isConfigured {
            let due = a.lastPolledAt.map { Date().timeIntervalSince($0) >= Double(state.pollHours) * 3_600 } ?? true
            if due { await poll(a, library: library, trigger: "schedule") }
            if MailWriting.journalDue(account: a) { await sendJournal(a, library: library) }
        }
    }

    // MARK: polling

    public func pollAll(library: LibraryModel) async {
        for a in state.accounts where a.enabled && a.isConfigured { await poll(a, library: library, trigger: "manual") }
    }

    public func poll(_ account: MailAccount, library: LibraryModel, trigger: String = "manual") async {
        guard !running.contains(account.id) else { return }
        running.insert(account.id); defer { running.remove(account.id); status = nil }
        error = nil
        let started = Date()
        var counts = Counts()
        var problems: [String] = []
        var acc = account
        status = "Opening \(account.email)…"
        let mailbox: any Mailbox
        do { mailbox = try await openMailbox(account) }
        catch {
            acc.lastError = error.localizedDescription; acc.lastPolledAt = Date(); update(acc)
            self.error = error.localizedDescription
            state.record(MailRun(accountId: account.id, startedAt: started, finishedAt: Date(), examined: 0, ingested: 0, forwards: 0, queued: 0, personal: 0, transactional: 0, errors: 1, problems: [error.localizedDescription], trigger: trigger)); save()
            return
        }
        defer { Task { await mailbox.close() } }
        do {
            status = "Looking for new mail…"
            let new = try await mailbox.newMessages(after: account.lastUID, limit: 5)
            var maxUID = account.lastUID
            var routing: Routing?
            for (uid, raw) in new {
                counts.examined += 1
                maxUID = max(maxUID, uid)
                let message = MailParser.parse(raw)
                status = "Reading “\(message.subject.prefix(60))”…"
                if state.seen(messageId: message.messageId) { counts.transactional += 1; continue }
                do {
                    if routing == nil { routing = await Routing(library: library) }
                    try await handle(message, raw: raw, uid: uid, account: acc, mailbox: mailbox, routing: routing!, library: library, counts: &counts)
                } catch {
                    counts.errors += 1; problems.append("\(message.subject.prefix(40)): \(error.localizedDescription)")
                }
                state.remember(messageId: message.messageId)
            }
            acc.lastUID = maxUID
            acc.lastError = nil
        } catch {
            counts.errors += 1; problems.append(error.localizedDescription); acc.lastError = error.localizedDescription
            self.error = error.localizedDescription
        }
        acc.lastPolledAt = Date()
        state.update(acc)
        state.record(MailRun(accountId: account.id, startedAt: started, finishedAt: Date(), examined: counts.examined, ingested: counts.ingested, forwards: counts.forwards, queued: counts.queued,
                             personal: counts.personal, transactional: counts.transactional, errors: counts.errors, problems: problems, trigger: trigger))
        save()
    }

    private struct Counts { var examined = 0, ingested = 0, forwards = 0, queued = 0, personal = 0, transactional = 0, errors = 0 }

    /// The notebooks' digest vectors, embedded once per poll.
    private struct Routing {
        var notebooks: [MailRouter.Notebook]
        var names: [(id: String, name: String)]
        @MainActor init(library: LibraryModel) async {
            var out: [MailRouter.Notebook] = []
            for nb in library.notebooks where !nb.digest.trimmingCharacters(in: .whitespaces).isEmpty {
                if let v = try? await library.documentVector(nb.digest) { out.append(.init(id: nb.id, name: nb.title, vector: v)) }
            }
            notebooks = out
            names = library.notebooks.map { ($0.id, $0.title) }
        }
    }

    private func handle(_ message: MailMessage, raw: Data, uid: Int, account: MailAccount, mailbox: any Mailbox, routing: Routing, library: LibraryModel, counts: inout Counts) async throws {
        // Forwards first: explicit intent, no model needed.
        if ForwardDetector.isForward(message) {
            let payload = ForwardDetector.extract(message)
            var target: (id: String, name: String)?
            var decision = MailRouter.Decision(verb: .noMatch, reason: "empty body")
            if let slug = ForwardDetector.hashtag(in: payload.wrapperSubject), let nb = MailRouter.notebook(slug: slug, among: routing.names) {
                target = nb; decision = .init(verb: .route, top: .init(notebookId: nb.id, notebookName: nb.name, confidence: 1), reason: "hashtag #\(slug)")
            } else if !payload.originalBody.trimmingCharacters(in: .whitespaces).isEmpty, let q = try? await library.documentVector(String(payload.originalBody.prefix(4_000))) {
                decision = MailRouter.decide(query: q, notebooks: routing.notebooks, senderCorrections: state.corrections(for: payload.originalSender.isEmpty ? message.sender : payload.originalSender))
                if decision.verb == .route, let top = decision.top { target = (top.notebookId, top.notebookName) }
            }
            if target == nil, let d = account.defaultForwardNotebookId, let nb = routing.names.first(where: { $0.id == d }) { target = nb }
            if let target {
                guard let sourceId = await ingestForward(payload, message: message, notebook: target.id, library: library) else { counts.errors += 1; return }
                counts.forwards += 1
                EventLog.record("mail_ingested", notebook: target.id, title: payload.originalSubject.isEmpty ? message.subject : payload.originalSubject, detail: "forwarded by \(payload.forwardedBy)")
                _ = sourceId
                if account.sendConfirmations, account.canSend, !payload.forwardedBy.isEmpty {
                    let subject = "Re: " + String((payload.originalSubject.isEmpty ? message.subject : payload.originalSubject).prefix(200))
                    await sendMail(account, to: payload.forwardedBy, subject: subject, text: MailWriting.confirmation(notebookName: target.name, sourceFilename: payload.originalSubject.isEmpty ? "forwarded email" : payload.originalSubject), html: nil, inReplyTo: message.messageId)
                }
                try? await mailbox.delete(uid: uid, messageId: message.messageId)
            } else {
                let item = MailQueueItem(kind: .forward, accountId: account.id, uid: uid, messageId: message.messageId, sender: payload.originalSender.isEmpty ? message.sender : payload.originalSender,
                                         subject: payload.originalSubject.isEmpty ? message.subject : payload.originalSubject, summary: String(payload.originalBody.prefix(300)), topicTags: [],
                                         topCandidate: decision.top, alternatives: decision.alternatives, decisionReason: decision.reason, raw: raw)
                if state.enqueue(item) { counts.queued += 1; EventLog.record("mail_queued", notebook: nil, title: item.subject, detail: "forward waiting for a notebook") }
            }
            return
        }

        let classification = await classify(message, library: library)
        switch classification.kind {
        case .personal:
            counts.personal += 1
            try? await mailbox.moveToPersonal(uid: uid, messageId: message.messageId, folder: state.personalFolder)
        case .transactional:
            counts.transactional += 1
            try? await mailbox.markRead(uid: uid, messageId: message.messageId)
        case .newsletter:
            var query = classification.summary
            if !classification.topicTags.isEmpty { query += "\n\nTopics: " + classification.topicTags.joined(separator: ", ") }
            let decision: MailRouter.Decision
            if query.trimmingCharacters(in: .whitespaces).isEmpty { decision = .init(verb: .noMatch, reason: "empty summary") }
            else if let q = try? await library.documentVector(query) { decision = MailRouter.decide(query: q, notebooks: routing.notebooks, senderCorrections: state.corrections(for: message.sender)) }
            else { decision = .init(verb: .noMatch, reason: "embedding failed") }
            if decision.verb == .route, let top = decision.top {
                guard await ingestNewsletter(message, classification: classification, notebook: top.notebookId, library: library) != nil else { counts.errors += 1; return }
                counts.ingested += 1
                EventLog.record("mail_ingested", notebook: top.notebookId, title: message.subject, detail: "newsletter from \(message.sender), \(decision.reason)")
                try? await mailbox.delete(uid: uid, messageId: message.messageId)
            } else {
                let item = MailQueueItem(kind: .newsletter, accountId: account.id, uid: uid, messageId: message.messageId, sender: message.sender, subject: message.subject, summary: classification.summary,
                                         topicTags: classification.topicTags, topCandidate: decision.top, alternatives: decision.alternatives, decisionReason: decision.reason, raw: raw)
                if state.enqueue(item) { counts.queued += 1; EventLog.record("mail_queued", notebook: nil, title: item.subject, detail: decision.reason) }
            }
        }
    }

    private func classify(_ message: MailMessage, library: LibraryModel) async -> MailClassification {
        guard GPUGuard.canStart, let gemma = await AgentText.model(library) else { return MailPrompts.heuristic(message) }
        status = "Sorting “\(message.subject.prefix(60))”…"
        let raw = await AgentText.generate(gemma, instructions: MailPrompts.classify, prompt: MailPrompts.classifyPrompt(message), tokens: 400, temperature: 0.2, label: "correspondent")
        let read = MailPrompts.readClassification(raw)
        // A model that failed to answer leaves the safe default; the header heuristics may still know a newsletter.
        if raw == nil { return MailPrompts.heuristic(message) }
        return read
    }

    private func ingestNewsletter(_ message: MailMessage, classification: MailClassification, notebook: String, library: LibraryModel) async -> String? {
        let text = MailText.sanitizeForModel(message.readableBody)
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        let hash = CorrespondentState.contentHash(text)
        if state.seen(hash: hash) { return "duplicate" }
        status = "Adding “\(message.subject.prefix(60))”…"
        let title = String(message.subject.prefix(200))
        let report = try? await library.ingestText(text, title: title, format: "email", notebook: notebook, url: nil)
        if report != nil { state.remember(messageId: message.messageId, hash: hash) }
        return report?.sourceId
    }

    private func ingestForward(_ payload: ForwardedPayload, message: MailMessage, notebook: String, library: LibraryModel) async -> String? {
        let body = payload.originalBody
        guard !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        let hash = CorrespondentState.contentHash(body)
        if state.seen(hash: hash) { return "duplicate" }
        status = "Adding the forward…"
        var text = body
        var header: [String] = []
        if !payload.originalSender.isEmpty { header.append("From: \(payload.originalSender)") }
        if !payload.originalDate.isEmpty { header.append("Date: \(payload.originalDate)") }
        if !header.isEmpty { text = header.joined(separator: "\n") + "\n\n" + body }
        let title = String((payload.originalSubject.isEmpty ? ForwardDetector.stripFwd(message.subject) : payload.originalSubject).prefix(200))
        let report = try? await library.ingestText(text, title: title.isEmpty ? "forwarded email" : title, format: "forward", notebook: notebook, url: nil)
        if report != nil { state.remember(messageId: message.messageId, hash: hash) }
        return report?.sourceId
    }

    // MARK: the queue

    /// Puts a queued message in `notebookId` (or its top candidate), learns the sender, deletes it from the inbox.
    public func approve(_ itemId: String, notebookId: String?, library: LibraryModel) async -> Bool {
        guard let item = state.queue.first(where: { $0.id == itemId }) else { return false }
        guard let target = notebookId ?? item.topCandidate?.notebookId else { error = "Pick a notebook first"; return false }
        running.insert(item.accountId); defer { running.remove(item.accountId); status = nil }
        let message = MailParser.parse(item.raw)
        let sourceId: String?
        if item.kind == .forward { sourceId = await ingestForward(ForwardDetector.extract(message), message: message, notebook: target, library: library) }
        else { sourceId = await ingestNewsletter(message, classification: MailClassification(kind: .newsletter, confidence: 1, summary: item.summary, topicTags: item.topicTags), notebook: target, library: library) }
        guard sourceId != nil else { error = "“\(item.subject)” could not be read"; return false }
        state.queue.removeAll { $0.id == itemId }
        state.recordCorrection(sender: item.sender, notebookId: target)
        EventLog.record("mail_ingested", notebook: target, title: item.subject, detail: "approved from the Correspondent queue")
        if let account = state.account(item.accountId) {
            if let box = try? await openMailbox(account) { try? await box.delete(uid: item.uid, messageId: item.messageId); await box.close() }
            if item.kind == .forward, account.sendConfirmations, account.canSend, let to = message.from?.address, !to.isEmpty {
                let name = library.notebooks.first { $0.id == target }?.title ?? target
                await sendMail(account, to: to, subject: "Re: " + String(item.subject.prefix(200)), text: MailWriting.confirmation(notebookName: name, sourceFilename: item.subject), html: nil, inReplyTo: item.messageId)
            }
        }
        save()
        return true
    }

    public func approveAll(notebookId: String?, library: LibraryModel) async {
        for item in queue { _ = await approve(item.id, notebookId: notebookId, library: library) }
    }

    public func dismiss(_ itemId: String) {
        state.queue.removeAll { $0.id == itemId }; save()
    }

    // MARK: sending

    /// Sends through Mail for the Mail path and over SMTP for IMAP accounts. Never throws: a failed confirmation is
    /// a status line, as in the old app.
    public func sendMail(_ account: MailAccount, to: String, subject: String, text: String, html: String?, inReplyTo: String?) async {
        status = "Replying to \(to)…"
        do {
            switch account.path {
            case .mailApp:
                #if os(macOS)
                try MailAppBridge.send(from: account.email, to: to, subject: subject, body: text)
                #endif
            case .imap:
                guard !account.smtpHost.isEmpty, let pw = MailSecrets.password(for: account.id) else { return }
                let data = SMTPWire.message(from: account.email, to: to, subject: subject, text: text, html: html, inReplyTo: inReplyTo)
                try await SMTPClient.send(host: account.smtpHost, port: account.smtpPort, tls: account.smtpUseTLS, user: account.imapUser.isEmpty ? account.email : account.imapUser, password: pw, from: account.email, to: to, message: data)
            }
        } catch { self.error = "Could not send to \(to): \(error.localizedDescription)" }
    }

    // MARK: the journal

    public func journalPreview(library: LibraryModel) -> (text: String, html: String)? {
        let names = Dictionary(uniqueKeysWithValues: library.notebooks.map { ($0.id, $0.title) })
        return MailWriting.journal(events: EventLog.since(Date().addingTimeInterval(-7 * 86_400)), notebookNames: names, since: Date().addingTimeInterval(-7 * 86_400))
    }

    /// Composes and mails this week's journal to the account's own address. False when the week was empty.
    @discardableResult public func sendJournal(_ account: MailAccount, library: LibraryModel) async -> Bool {
        guard account.canSend, let body = journalPreview(library: library) else { return false }
        await sendMail(account, to: account.email, subject: "Your LocalBook weekly journal", text: body.text, html: body.html, inReplyTo: nil)
        guard error == nil else { return false }
        var a = account; a.lastJournalAt = Date(); update(a)
        EventLog.record("journal_sent", notebook: nil, title: "Weekly journal to \(account.email)")
        return true
    }

    // MARK: mailboxes

    private func openMailbox(_ account: MailAccount) async throws -> any Mailbox {
        switch account.path {
        case .imap:
            guard let pw = MailSecrets.password(for: account.id) else { throw MailConnection.Problem.refused("No password saved for \(account.email); edit the account") }
            let client = IMAPClient(host: account.imapHost, port: account.imapPort, tls: account.useSSL)
            try await client.connect(user: account.imapUser.isEmpty ? account.email : account.imapUser, password: pw)
            try await client.select()
            return IMAPMailbox(client: client)
        case .mailApp:
            guard let folder = mailFolder else { throw MailConnection.Problem.refused("Choose your Mail folder in the Correspondent's Accounts tab") }
            guard let index = MailAppStore.indexURL(in: folder) else { throw MailConnection.Problem.refused("No Envelope Index under \(folder.path); pick ~/Library/Mail and allow Full Disk Access") }
            return MailAppMailbox(folder: folder, index: index, mailboxURL: account.mailboxURL, cache: emlxCache[account.id] ?? [:]) { [weak self] c in self?.emlxCache[account.id] = c }
        }
    }
}

/// What a poll needs from an inbox, whichever path it is.
protocol Mailbox: Sendable {
    /// Messages after `uid` (an IMAP UID or a Mail ROWID), oldest first, at most `limit`.
    func newMessages(after uid: Int, limit: Int) async throws -> [(uid: Int, raw: Data)]
    func markRead(uid: Int, messageId: String) async throws
    func moveToPersonal(uid: Int, messageId: String, folder: String) async throws
    func delete(uid: Int, messageId: String) async throws
    func close() async
}

struct IMAPMailbox: Mailbox {
    let client: IMAPClient
    func newMessages(after uid: Int, limit: Int) async throws -> [(uid: Int, raw: Data)] {
        let uids = try await client.unseenUIDs(after: uid).prefix(limit)
        var out: [(Int, Data)] = []
        for u in uids { if let d = try await client.fetch(uid: u) { out.append((u, d)) } }
        return out
    }
    func markRead(uid: Int, messageId: String) async throws { try await client.markSeen(uid: uid) }
    func moveToPersonal(uid: Int, messageId: String, folder: String) async throws { try await client.ensureMailbox(folder); try await client.move(uid: uid, to: folder) }
    func delete(uid: Int, messageId: String) async throws { try await client.delete(uid: uid) }
    func close() async { await client.logout() }
}

/// Mail.app's store: rows from the index, bytes from the emlx files, actions through Mail itself.
final class MailAppMailbox: Mailbox, @unchecked Sendable {
    let folder: URL
    let index: URL
    let mailboxURL: String
    private var cache: [Int: URL]
    private let saveCache: @MainActor ([Int: URL]) -> Void
    init(folder: URL, index: URL, mailboxURL: String, cache: [Int: URL], saveCache: @escaping @MainActor ([Int: URL]) -> Void) {
        self.folder = folder; self.index = index; self.mailboxURL = mailboxURL; self.cache = cache; self.saveCache = saveCache
    }
    func newMessages(after uid: Int, limit: Int) async throws -> [(uid: Int, raw: Data)] {
        let rows = try MailAppStore.newMessages(index: index, mailboxURL: mailboxURL, afterRowId: uid, limit: limit)
        var out: [(Int, Data)] = []
        for r in rows {
            guard let file = MailAppStore.messageFile(rowId: r.rowId, mailFolder: folder, mailboxURL: mailboxURL, cache: &cache) else { continue }
            if let raw = try? MailAppStore.rawMessage(at: file) { out.append((r.rowId, raw)) }
        }
        return out
    }
    func markRead(uid: Int, messageId: String) async throws {
        #if os(macOS)
        try MailAppBridge.markRead(messageId: messageId)
        #endif
    }
    func moveToPersonal(uid: Int, messageId: String, folder: String) async throws {
        #if os(macOS)
        try MailAppBridge.move(messageId: messageId, toMailbox: folder, account: nil)
        #endif
    }
    func delete(uid: Int, messageId: String) async throws {
        #if os(macOS)
        try MailAppBridge.delete(messageId: messageId)
        #endif
    }
    func close() async { let c = cache; await saveCache(c) }
}
