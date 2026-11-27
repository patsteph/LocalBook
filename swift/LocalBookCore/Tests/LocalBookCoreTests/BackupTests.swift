import CryptoKit
import Foundation
import Testing
@testable import LocalBookCore

/// Held to `backup_golden.json`, written by scripts/ci/backup_golden.py from the old app's keyvault and backup_service.
@Suite struct BackupTests {
    struct Golden: Decodable {
        var phrase: String, seed_hex: String, private_hex: String, public_b64: String, device_key_hex: String, content_key_hex: String
        var files: [String: String], archive_b64: String, tar_sha256: String, bad_phrase: String
    }

    static func golden() throws -> Golden {
        let url = try #require(Bundle.module.url(forResource: "backup_golden", withExtension: "json", subdirectory: "Fixtures"))
        return try JSONDecoder().decode(Golden.self, from: Data(contentsOf: url))
    }

    static func bytes(hex: String) -> Data {
        var d = Data(); var i = hex.startIndex
        while i < hex.endIndex { let j = hex.index(i, offsetBy: 2); d.append(UInt8(hex[i..<j], radix: 16)!); i = j }
        return d
    }

    @Test func phraseSeedAndKey() throws {
        let g = try Self.golden()
        try BIP39.check(g.phrase)
        #expect(BIP39.seed(g.phrase) == Self.bytes(hex: g.seed_hex))
        #expect(try RecoveryKey.publicKey(phrase: g.phrase).base64EncodedString() == g.public_b64)
        #expect(throws: BIP39.PhraseError.checksum) { try BIP39.check(g.bad_phrase) }
        #expect(throws: BIP39.PhraseError.wordCount(3)) { try BIP39.check("legal winner thank") }
        #expect(throws: BIP39.PhraseError.unknownWord("zzz")) { try BIP39.check(g.phrase.replacingOccurrences(of: "title", with: "zzz")) }
        // Case and spacing do not matter; the standard test vector's entropy is 0x7f × 32.
        #expect(try BIP39.check("  " + g.phrase.uppercased().replacingOccurrences(of: " ", with: "   ")) == Data(repeating: 0x7f, count: 32))
        #expect(BIP39.phrase(entropy: Data(repeating: 0x7f, count: 32)) == g.phrase)
        #expect(BIP39.words.count == 2048)
    }

    @Test func freshPhrases() throws {
        let a = BIP39.generate(), b = BIP39.generate()
        #expect(a != b)
        #expect(a.split(separator: " ").count == 24)
        try BIP39.check(a)
        #expect(try RecoveryKey.publicKey(phrase: a).count == 32)
    }

    @Test func opensOldArchiveWithPhrase() throws {
        let g = try Self.golden()
        let archive = try #require(Data(base64Encoded: g.archive_b64))
        let header = try BackupEnvelope.header(of: archive)
        #expect(header.device_id == "golden-device")
        #expect(header.recipients.map(\.kind) == ["recovery", "device"])
        let (_, tar) = try BackupEnvelope.open(archive, deviceKey: nil, phrase: g.phrase)
        #expect(SHA256.hash(data: tar).map { String(format: "%02x", $0) }.joined() == g.tar_sha256)
        let (manifest, files) = try BackupPayload.unpack(tar: tar)
        #expect(manifest.deviceId == "golden-device")
        #expect(Dictionary(uniqueKeysWithValues: files.map { ($0.name, String(decoding: $0.data, as: UTF8.self)) }) == g.files)
        #expect(BackupPayload.mismatches(manifest: manifest, files: files).isEmpty)
        #expect(!manifest.isFromThisApp)
    }

    @Test func opensOldArchiveWithDeviceKey() throws {
        let g = try Self.golden()
        let archive = try #require(Data(base64Encoded: g.archive_b64))
        let (_, tar) = try BackupEnvelope.open(archive, deviceKey: SymmetricKey(data: Self.bytes(hex: g.device_key_hex)), phrase: nil)
        #expect(SHA256.hash(data: tar).map { String(format: "%02x", $0) }.joined() == g.tar_sha256)
    }

    @Test func refusesWrongKeysAndEditedHeader() throws {
        let g = try Self.golden()
        var archive = try #require(Data(base64Encoded: g.archive_b64))
        #expect(throws: BackupEnvelope.OpenError.locked(triedPhrase: false)) { try BackupEnvelope.open(archive, deviceKey: SymmetricKey(size: .bits256), phrase: nil) }
        #expect(throws: BackupEnvelope.OpenError.locked(triedPhrase: true)) { try BackupEnvelope.open(archive, deviceKey: nil, phrase: BIP39.generate()) }
        #expect(throws: BackupEnvelope.OpenError.notABackup) { try BackupEnvelope.open(Data("hello".utf8), deviceKey: nil, phrase: nil) }
        // Back-dating the header: same length, so it still parses, but it is the body's AAD.
        let headerRange = BackupEnvelope.magic.count + 4
        let needle = Data("2026-11-27T12:00:00+00:00".utf8)
        let at = try #require(archive[headerRange...].range(of: needle))
        archive.replaceSubrange(at, with: Data("2025-11-27T12:00:00+00:00".utf8))
        #expect(throws: BackupEnvelope.OpenError.damaged) { try BackupEnvelope.open(archive, deviceKey: nil, phrase: g.phrase) }
    }

    @Test func tarWriterMatchesPython() throws {
        let g = try Self.golden()
        let archive = try #require(Data(base64Encoded: g.archive_b64))
        let (_, tar) = try BackupEnvelope.open(archive, deviceKey: nil, phrase: g.phrase)
        let entries = try Tar.read(tar)
        #expect(entries.map(\.name) == ["data/Library/notes.txt", "data/MANIFEST.json", "data/settings.json"])
        #expect(entries.allSatisfy { $0.mode == 0o600 && $0.mtime == 1_764_000_000 })
        let again = try Tar.write(entries)
        #expect(again == tar)
        #expect(tar.count == Tar.recordSize)
        // Long names go through the prefix field and come back whole.
        let long = "data/" + String(repeating: "folder/", count: 20) + "file.txt"
        let round = try Tar.read(try Tar.write([Tar.Entry(name: long, data: Data("x".utf8), mtime: 1)]))
        #expect(round.first?.name == long)
        #expect(throws: Tar.TarError.nameTooLong(String(repeating: "a", count: 300))) { try Tar.write([Tar.Entry(name: String(repeating: "a", count: 300), data: Data(), mtime: 1)]) }
    }

    @Test func sealsAndOpensBothWays() throws {
        let phrase = BIP39.generate()
        let device = SymmetricKey(size: .bits256)
        let files = [Tar.Entry(name: "Library/library.sqlite", data: Data(repeating: 7, count: 4_000), mtime: 10), Tar.Entry(name: "proposals.json", data: Data("[]".utf8), mtime: 11)]
        let staged = BackupBuilder.Staged(files: files, rowCounts: ["Library/library.sqlite": ["chunks": 12]], skipped: [], mediaBytesLeftOut: 0)
        let manifest = BackupBuilder.manifest(staged: staged, deviceId: "mac-1", deviceName: "Test Mac", includeMedia: false, recoveryPublicKey: try RecoveryKey.publicKey(phrase: phrase), createdAt: "2026-11-27T13:00:00Z")
        #expect(manifest.isFromThisApp)
        #expect(manifest.totalBytes == 4_002)
        let tar = try BackupPayload.pack(manifest: manifest, files: files)
        let archive = try BackupEnvelope.seal(tar: tar, createdAt: manifest.createdAt, deviceId: "mac-1", deviceKey: device, recoveryPublicKey: try RecoveryKey.publicKey(phrase: phrase))
        #expect(archive.prefix(6) == BackupEnvelope.magic)
        for (key, pass) in [(device as SymmetricKey?, nil as String?), (nil, phrase)] {
            let (header, back) = try BackupEnvelope.open(archive, deviceKey: key, phrase: pass)
            #expect(header.device_id == "mac-1")
            #expect(back == tar)
            let (m, f) = try BackupPayload.unpack(tar: back)
            #expect(m.rowCounts["Library/library.sqlite"]?["chunks"] == 12)
            #expect(f == files)
            #expect(BackupPayload.mismatches(manifest: m, files: f).isEmpty)
        }
        #expect(throws: BackupEnvelope.OpenError.noRecipient) { try BackupEnvelope.seal(tar: tar, createdAt: "", deviceId: "x", deviceKey: nil, recoveryPublicKey: nil) }
        // A changed file inside is caught by the manifest.
        var broken = files; broken[1].data = Data("[1]".utf8)
        #expect(BackupPayload.mismatches(manifest: manifest, files: broken) == ["proposals.json"])
    }

    @Test func stagesRestoresAndKeepsReplaced() throws {
        let base = FileManager.default.temporaryDirectory.appending(path: "lb-backup-\(UUID().uuidString)", directoryHint: .isDirectory)
        defer { try? FileManager.default.removeItem(at: base) }
        let lib = base.appending(path: "Library", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: lib, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: base.appending(path: "LocalBookLab/hub/x"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: base.appending(path: "Studio"), withIntermediateDirectories: true)
        let store = try LibraryStore(path: lib.appending(path: "library.sqlite"))
        _ = store
        try Data("weights".utf8).write(to: base.appending(path: "LocalBookLab/hub/x/model.safetensors"))
        try Data("podcast".utf8).write(to: base.appending(path: "Studio/a.m4a"))
        try Data("{\"a\":1}".utf8).write(to: base.appending(path: "Studio/a.json"))
        try Data("keys".utf8).write(to: base.appending(path: "companion_keys.json"))
        try Data("log".utf8).write(to: base.appending(path: "companion_calls.json"))

        let staged = try BackupBuilder.stage(base: base, includeMedia: false)
        let names = staged.files.map(\.name)
        #expect(names.contains("Library/library.sqlite"))
        #expect(names.contains("Studio/a.json") && names.contains("companion_keys.json"))
        #expect(!names.contains("Studio/a.m4a") && !names.contains("LocalBookLab/hub/x/model.safetensors") && !names.contains("companion_calls.json"))
        #expect(staged.mediaBytesLeftOut == 7)
        #expect(staged.rowCounts["Library/library.sqlite"]?["notebooks"] == 0)
        #expect(try BackupBuilder.stage(base: base, includeMedia: true).files.map(\.name).contains("Studio/a.m4a"))

        let phrase = BIP39.generate()
        let made = try BackupBuilder.makeArchive(base: base, deviceId: "mac-1", deviceName: "Commodore 64 Mac", deviceKey: nil, recoveryPublicKey: try RecoveryKey.publicKey(phrase: phrase), includeMedia: false)
        #expect(made.manifest.files.count == names.count)
        #expect(try BackupRestore.verify(archive: made.archive, deviceKey: nil, phrase: phrase).deviceName == "Commodore 64 Mac")

        // Change the live folder, then stage the restore and apply it as a launch would.
        try Data("{\"a\":2}".utf8).write(to: base.appending(path: "Studio/a.json"))
        try Data("{\"b\":1}".utf8).write(to: base.appending(path: "Studio/b.json"))
        try Data("stale".utf8).write(to: lib.appending(path: "library.sqlite-wal"))
        try BackupRestore.stage(archive: made.archive, base: base, deviceKey: nil, phrase: phrase)
        #expect(BackupRestore.hasPending(base: base))
        #expect(throws: BackupError.pendingExists) { try BackupRestore.stage(archive: made.archive, base: base, deviceKey: nil, phrase: phrase) }
        // The pending folder itself is never backed up.
        #expect(!(try BackupBuilder.stage(base: base, includeMedia: false).files.map(\.name).contains { $0.hasPrefix("Restore-pending") }))
        let replaced = try #require(try BackupRestore.applyPending(base: base))
        #expect(!BackupRestore.hasPending(base: base))
        #expect(try String(contentsOf: base.appending(path: "Studio/a.json"), encoding: .utf8) == "{\"a\":1}")
        #expect(try String(contentsOf: replaced.appending(path: "Studio/a.json"), encoding: .utf8) == "{\"a\":2}")
        #expect(FileManager.default.fileExists(atPath: base.appending(path: "Studio/b.json").path))       // not in the backup: kept
        #expect(!FileManager.default.fileExists(atPath: lib.appending(path: "library.sqlite-wal").path))
        #expect(FileManager.default.fileExists(atPath: replaced.appending(path: "restored-MANIFEST.json").path))
        #expect(try BackupRestore.applyPending(base: base) == nil)

        // The old app's archive is refused for restore, with a pointer to the import.
        let g = try Self.golden()
        #expect(throws: BackupError.oldApp) { try BackupRestore.stage(archive: try #require(Data(base64Encoded: g.archive_b64)), base: base, deviceKey: nil, phrase: g.phrase) }
    }

    @Test func namesAndExclusions() {
        #expect(BackupBuilder.fileName(deviceName: "Pat's MacBook Pro", at: Date(timeIntervalSince1970: 0)).hasPrefix("LocalBook-Pat-s-MacBook-Pro-"))
        #expect(BackupBuilder.fileName(deviceName: "x").hasSuffix(".lbbackup"))
        #expect(BackupBuilder.isExcluded("LocalBookLab/hub/models--x/blobs/y"))
        #expect(BackupBuilder.isExcluded("Library/library.sqlite-shm"))
        #expect(BackupBuilder.isExcluded("Restore-pending/MANIFEST.json"))
        #expect(BackupBuilder.isExcluded("keys/recovery.pub"))
        #expect(!BackupBuilder.isExcluded("LocalBookLab/Sources/nb/file.pdf"))
        #expect(!BackupBuilder.isExcluded("Library/memory.sqlite"))
        #expect(BackupBuilder.isMedia("LocalBookLab/Meetings/m.m4a") && !BackupBuilder.isMedia("Studio/a.json"))
    }
}
