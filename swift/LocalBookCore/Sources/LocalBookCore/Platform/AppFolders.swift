import Foundation

/// Where the engine keeps its files.
///
/// The iPhone app stores everything under Application Support with the folder names the Lab chose
/// (`Library/`, `LocalBookLab/`), and Phase 1 must not move a byte, so those names stay the default on iOS.
/// The Mac app sets `AppFolders.base` once at launch to its own folder (`LocalBook (Swift)/` during the
/// side-by-side phases so the Python app's `LocalBook/` folder is never touched). Everything in the package
/// asks here instead of naming a directory.
public enum AppFolders {
    /// The root under which this app keeps its files. Set once at launch; defaults to the Lab's layout on iOS
    /// and to a separate folder on the Mac.
    nonisolated(unsafe) public static var base: URL = defaultBase

    public static var defaultBase: URL {
        #if os(iOS)
        return URL.applicationSupportDirectory
        #else
        return URL.applicationSupportDirectory.appending(path: "LocalBook (Swift)", directoryHint: .isDirectory)
        #endif
    }

    /// The SQLite library (chunks, vectors, notebooks).
    public static var library: URL { base.appending(path: "Library", directoryHint: .isDirectory) }

    /// Downloaded models (the search model, the answer model, voices).
    public static var models: URL { base.appending(path: "LocalBookLab", directoryHint: .isDirectory) }

    /// Studio items, meetings, photos and other files the user made.
    public static var userFiles: URL { base.appending(path: "LocalBookLab", directoryHint: .isDirectory) }

    /// Makes a folder if it is missing and returns it.
    @discardableResult
    public static func ensure(_ url: URL) throws -> URL {
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}
