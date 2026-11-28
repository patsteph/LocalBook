import Testing
@testable import LocalBookCore

@Suite struct SyncRoleTests {
    @Test func libraryMacRunsSchedules() {
        #expect(SyncRole.role(pairedWithAnotherMac: false) == .library)
        #expect(SyncRole.runsSchedules(role: .library, companionOptedIn: false))
        #expect(SyncRole.runsSchedules(role: .library, companionOptedIn: true))
    }

    @Test func companionRunsOnlyWhenAsked() {
        #expect(SyncRole.role(pairedWithAnotherMac: true) == .companion)
        #expect(!SyncRole.runsSchedules(role: .companion, companionOptedIn: false))
        #expect(SyncRole.runsSchedules(role: .companion, companionOptedIn: true))
    }

    @Test func linesNameTheThreeAgents() {
        for (role, opt) in [(SyncRole.library, false), (.companion, false), (.companion, true)] {
            let line = SyncRole.line(role: role, companionOptedIn: opt)
            #expect(line.contains("Collector") && line.contains("Correspondent") && line.contains("People profiler"))
        }
        #expect(SyncRole.line(role: .companion, companionOptedIn: false).contains("Now buttons"))
    }
}
