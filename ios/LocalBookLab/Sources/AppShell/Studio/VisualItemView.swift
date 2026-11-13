import LocalBookEngines
import LocalBookCore
import SwiftUI

/// The visual screen: the 16:9 slide fitted to the screen, with pinch to zoom, drag to move and double-tap to zoom in or
/// fit, plus save-as-image sharing (2K).
struct VisualItemView: View {
    let spec: VisualSpec
    let title: String
    @State private var picture: PlatformImage?
    @State private var scale: CGFloat = 1
    @State private var committed: CGFloat = 1
    @State private var fit: CGFloat = 1
    @State private var expanded: Set<Int> = []

    var body: some View {
        GeometryReader { geo in
            let base = min(geo.size.width / VisualSlide.size.width, geo.size.height / VisualSlide.size.height)
            let w = VisualSlide.size.width * base * scale, h = VisualSlide.size.height * base * scale
            ScrollView([.horizontal, .vertical]) {
                VisualSlide(spec: spec, fit: fit, expanded: $expanded)
                    .scaleEffect(base * scale, anchor: .topLeading)
                    .frame(width: w, height: h, alignment: .topLeading)
                    .frame(minWidth: geo.size.width, minHeight: geo.size.height)       // centred when smaller than the screen
            }
            .simultaneousGesture(
                MagnifyGesture()
                    .onChanged { scale = min(4, max(1, committed * $0.magnification)) }
                    .onEnded { _ in committed = scale }
            )
            .onTapGesture(count: 2) { withAnimation(.snappy) { scale = scale > 1.01 ? 1 : 2; committed = scale } }
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                if let picture { ShareLink(item: Image(platformImage: picture), preview: SharePreview(title, image: Image(platformImage: picture))) }
            }
            ToolbarItem(placement: .topBarLeading) {
                if scale > 1.01 {
                    Button { withAnimation(.snappy) { scale = 1; committed = 1 } } label: { Image(systemName: "arrow.down.right.and.arrow.up.left") }
                        .accessibilityLabel("Fit to screen")
                }
            }
        }
        .task {
            fit = VisualSlide.fit(for: spec)
            picture = VisualSlide.image(for: spec, fit: fit)
        }
        .onChange(of: expanded) { _, now in                     // open branches make the slide fuller: shrink it to stay 16:9
            withAnimation(.snappy) { fit = VisualSlide.fit(for: spec, expanded: now) }
        }
    }
}
