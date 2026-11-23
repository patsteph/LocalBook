import CoreImage
import Foundation
import LocalBookCore
import MLXLMCommon
import SwiftUI

/// The critic: the answer model looks at the finished picture and scores it on the five axes the old app's critic
/// scored. Mac only in practice (the phone's model has no vision tower), off by default, one short vision call.
public enum VisualCritic {
    /// Draws the spec at a size the vision encoder can use and asks the model for its verdict. nil when the model
    /// could not look (no vision tower, a stopped run) or said nothing readable.
    @MainActor public static func judge(_ spec: VisualSpec, with model: ModelContainer) async -> VisualCritique? {
        guard let picture = VisualSlide.image(for: spec, fit: 1, width: 1200), let image = Self.ciImage(picture) else { return nil }
        let session = ChatSession(model, instructions: VisualCritique.instructions, generateParameters: GenerateParameters(maxTokens: 90, temperature: 0))
        do {
            let reply = try await session.respond(to: VisualCritique.prompt(title: spec.title, form: spec.layout.label), image: .ciImage(image))
            let verdict = VisualCritique.parse(reply)
            SoakTrace.log("visual critic: \(verdict?.line ?? "unreadable") from “\(reply.prefix(120).replacingOccurrences(of: "\n", with: " "))”")
            return verdict
        } catch {
            SoakTrace.log("visual critic failed: \(error.localizedDescription)")
            return nil
        }
    }

    static func ciImage(_ image: PlatformImage) -> CIImage? {
        #if canImport(AppKit)
        guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        return CIImage(cgImage: cg)
        #else
        return CIImage(image: image)
        #endif
    }
}
