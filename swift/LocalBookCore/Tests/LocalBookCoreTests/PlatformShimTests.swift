import Foundation
import Testing
@testable import LocalBookCore

@Suite("Platform shims")
struct PlatformShimTests {
    @Test("lifecycle names are distinct and stable")
    func lifecycleNames() {
        let names = [AppLifecycle.willResignActive, AppLifecycle.didBecomeActive, AppLifecycle.didEnterBackground,
                     AppLifecycle.willEnterForeground, AppLifecycle.protectedDataWillBecomeUnavailable,
                     AppLifecycle.protectedDataDidBecomeAvailable, AppLifecycle.didReceiveMemoryWarning]
        #expect(Set(names.map(\.rawValue)).count == names.count)
    }

    @Test("GPU withdrawal follows the platform")
    func gpuWithdrawal() {
        #if os(iOS)
        #expect(AppLifecycle.backgroundWithdrawsGPU)
        #else
        #expect(!AppLifecycle.backgroundWithdrawsGPU)
        #endif
    }

    @Test("device description has a model, a system and memory")
    func deviceDescription() {
        #expect(!DeviceDescription.model.isEmpty)
        #expect(DeviceDescription.system.contains(" "))
        #expect(DeviceDescription.memoryGB > 0)
        #expect(["nominal", "fair", "serious", "critical", "unknown"].contains(DeviceDescription.thermalState))
    }

    @Test("folders hang off the base and keep the iPhone's names")
    func folders() throws {
        let saved = AppFolders.base
        defer { AppFolders.base = saved }
        AppFolders.base = FileManager.default.temporaryDirectory.appending(path: "LocalBookCoreTests-\(UUID().uuidString)")
        #expect(AppFolders.library.lastPathComponent == "Library")
        #expect(AppFolders.models.lastPathComponent == "LocalBookLab")
        #expect(AppFolders.library.path().hasPrefix(AppFolders.base.path()))
        let made = try AppFolders.ensure(AppFolders.library)
        var isDir: ObjCBool = false
        #expect(FileManager.default.fileExists(atPath: made.path(), isDirectory: &isDir) && isDir.boolValue)
        try? FileManager.default.removeItem(at: AppFolders.base)
    }

    @Test("keep-awake ends once")
    @MainActor func keepAwake() {
        let hold = KeepAwake.begin(reason: "test")
        hold.end()
        hold.end()
    }

    @Test("fixtures are bundled")
    func fixtures() throws {
        let url = try #require(Bundle.module.url(forResource: "README", withExtension: "md", subdirectory: "Fixtures"))
        #expect(FileManager.default.fileExists(atPath: url.path()))
    }
}
