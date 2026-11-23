import CoreGraphics
import Foundation

/// Where a painted backdrop stands: not on this device, on its way, ready to paint, or not coming.
public enum VisualImageryState: Equatable, Sendable {
    case absent
    /// 0…1 of the download, and the part being fetched.
    case downloading(Double, String)
    case ready
    case failed(String)

    public var isReady: Bool { self == .ready }
}

/// An image model the engine can ask for a backdrop (`VisualHero`). The Mac app provides one (FLUX.2 Klein through
/// MLX, downloaded after launch); the phone provides none, so its visuals draw the band and never load an image
/// model. The engine asks only when `state` is ready, so a visual never waits on a download.
@MainActor public protocol VisualImagery: AnyObject {
    /// The model's name, for logs and Settings.
    var name: String { get }
    var state: VisualImageryState { get }
    /// Downloads what is missing, once; sets `state` as it goes. Called after the app's other models are warm.
    func prepare() async
    /// Paints `prompt` at `width` × `height` pixels. `seed` makes the same ask paint the same picture.
    func hero(prompt: String, width: Int, height: Int, seed: UInt64) async throws -> CGImage
    /// Paints `picture` again from itself, as `prompt` asks (the second, detail pass). A model that cannot returns
    /// `picture` unchanged.
    func refine(_ picture: CGImage, prompt: String, seed: UInt64) async throws -> CGImage
}
