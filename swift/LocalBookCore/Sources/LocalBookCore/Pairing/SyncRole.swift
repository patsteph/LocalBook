import Foundation

/// Which Mac runs the agents' schedules when two Macs share a library.
///
/// Mac-to-Mac sync is the phone link again: a second Swift Mac pairs with the Mac that holds the library exactly as
/// the iPhone does (`MacLink` ↔ `PhoneHost`), pulls the notebooks it is given diff by diff, and sends its notes,
/// Studio items and edits back. What the old app's `roles.py` added on top was one rule: scheduled collections run
/// on one Mac only, so two Macs do not both poll the same feeds and inboxes and store the same items twice. The
/// library Mac runs them; a companion Mac runs them only when its owner asks.
public enum SyncRole: Sendable, Equatable {
    /// Holds the library; paired devices copy from it. Schedules run here.
    case library
    /// Paired with a library Mac; a copy. Schedules run here only when `companionRunsSchedules` is on.
    case companion

    public static let companionRunsSchedulesKey = "LocalBook.companionRunsSchedules"

    /// Whether the Collector, the Correspondent and the People profiler run their schedules on this Mac.
    /// "Now" buttons are never held back by this; only the clocks are.
    public static func runsSchedules(role: SyncRole, companionOptedIn: Bool) -> Bool {
        switch role {
        case .library: return true
        case .companion: return companionOptedIn
        }
    }

    public static func role(pairedWithAnotherMac: Bool) -> SyncRole { pairedWithAnotherMac ? .companion : .library }

    /// The line Settings › Sync shows under the schedules toggle.
    public static func line(role: SyncRole, companionOptedIn: Bool) -> String {
        switch role {
        case .library:
            return "This Mac holds the library, so the Collector, the Correspondent and the People profiler run their schedules here. A Mac paired with this one copies what they bring in."
        case .companion where companionOptedIn:
            return "The Collector, the Correspondent and the People profiler run their schedules on this Mac as well as on the Mac it is paired with. Both poll the same feeds and inboxes; items are matched by their ids and content, so duplicates are rare but the work is done twice."
        case .companion:
            return "This Mac is a copy of the library on the Mac it is paired with, which runs the Collector, the Correspondent and the People profiler. What they bring in reaches this Mac with the next sync; the Now buttons still work here."
        }
    }
}
