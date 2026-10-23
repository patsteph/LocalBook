import AVFoundation
import SwiftUI

/// How a video looks: the palette and type of every slide.
enum VideoStyle: String, CaseIterable, Identifiable, Sendable {
    case clean, bold, story
    var id: String { rawValue }
    var label: String { switch self { case .clean: "Clean"; case .bold: "Bold"; case .story: "Story" } }
    var background: Color { switch self { case .clean: Color(red: 0.97, green: 0.97, blue: 0.98); case .bold: Color(red: 0.07, green: 0.09, blue: 0.20); case .story: Color(red: 0.98, green: 0.94, blue: 0.86) } }
    var text: Color { switch self { case .clean: Color(red: 0.10, green: 0.12, blue: 0.18); case .bold: .white; case .story: Color(red: 0.25, green: 0.17, blue: 0.12) } }
    var accent: Color { switch self { case .clean: Color(red: 0.16, green: 0.42, blue: 0.95); case .bold: Color(red: 1.0, green: 0.78, blue: 0.20); case .story: Color(red: 0.80, green: 0.36, blue: 0.22) } }
    var design: Font.Design { self == .story ? .serif : .default }
}

/// One slide, drawn at 1280×720 so it can be turned into a video frame.
struct VideoSlideView: View {
    let title: String
    let points: [String]
    let number: Int
    let total: Int
    let style: VideoStyle

    var body: some View {
        ZStack(alignment: .topLeading) {
            style.background
            VStack(alignment: .leading, spacing: 34) {
                RoundedRectangle(cornerRadius: 4).fill(style.accent).frame(width: 90, height: 8)
                Text(title).font(.system(size: 66, weight: .bold, design: style.design)).foregroundStyle(style.text)
                    .lineLimit(3).minimumScaleFactor(0.7).fixedSize(horizontal: false, vertical: true)
                VStack(alignment: .leading, spacing: 20) {
                    ForEach(points.indices, id: \.self) { i in
                        HStack(alignment: .firstTextBaseline, spacing: 18) {
                            Circle().fill(style.accent).frame(width: 14, height: 14).offset(y: -4)
                            Text(points[i]).font(.system(size: 36, weight: .regular, design: style.design)).foregroundStyle(style.text.opacity(0.88))
                                .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                Spacer(minLength: 0)
                HStack(spacing: 8) {
                    ForEach(0..<total, id: \.self) { i in
                        Capsule().fill(i == number - 1 ? style.accent : style.text.opacity(0.18)).frame(width: i == number - 1 ? 38 : 12, height: 10)
                    }
                }
            }
            .padding(.horizontal, 90).padding(.vertical, 70)
        }
        .frame(width: 1280, height: 720)
    }
}

/// Turns slide images and a narration file into an MP4 on the phone: stills for the picture, the narration for the sound.
enum VideoMaker {
    static let size = CGSize(width: 1280, height: 720)

    struct Failure: Error, LocalizedError { let text: String; var errorDescription: String? { text } }

    /// `frames` pairs each slide image with the time (seconds) it appears; the picture ends at `duration`.
    static func write(frames: [(image: CGImage, start: Double)], duration: Double, narration: URL, to output: URL) async throws {
        let temp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mp4")
        defer { try? FileManager.default.removeItem(at: temp) }

        let writer = try AVAssetWriter(outputURL: temp, fileType: .mp4)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: Int(size.width), AVVideoHeightKey: Int(size.height),
        ])
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: Int(size.width), kCVPixelBufferHeightKey as String: Int(size.height),
        ])
        guard writer.canAdd(input) else { throw Failure(text: "Couldn't set up the video.") }
        writer.add(input)
        guard writer.startWriting() else { throw Failure(text: writer.error?.localizedDescription ?? "Couldn't start the video.") }
        writer.startSession(atSourceTime: .zero)

        func append(_ image: CGImage, at seconds: Double) async throws {
            while !input.isReadyForMoreMediaData { try await Task.sleep(for: .milliseconds(15)) }
            guard let buffer = pixelBuffer(image), adaptor.append(buffer, withPresentationTime: CMTime(seconds: seconds, preferredTimescale: 600)) else {
                throw Failure(text: writer.error?.localizedDescription ?? "Couldn't write a slide.")
            }
        }
        for frame in frames { try await append(frame.image, at: frame.start) }
        if let last = frames.last, duration - last.start > 0.5 { try await append(last.image, at: duration - 0.1) }
        input.markAsFinished()
        writer.endSession(atSourceTime: CMTime(seconds: duration, preferredTimescale: 600))
        await writer.finishWriting()
        guard writer.status == .completed else { throw Failure(text: writer.error?.localizedDescription ?? "Couldn't finish the video.") }

        // Join the picture and the narration without re-encoding either.
        let picture = AVURLAsset(url: temp), sound = AVURLAsset(url: narration)
        let composition = AVMutableComposition()
        guard let vSource = try await picture.loadTracks(withMediaType: .video).first,
              let aSource = try await sound.loadTracks(withMediaType: .audio).first,
              let vTrack = composition.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid),
              let aTrack = composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)
        else { throw Failure(text: "Couldn't join the picture and sound.") }
        let vLength = try await picture.load(.duration), aLength = try await sound.load(.duration)
        let both = CMTimeMinimum(vLength, aLength)
        try vTrack.insertTimeRange(CMTimeRange(start: .zero, duration: both), of: vSource, at: .zero)
        try aTrack.insertTimeRange(CMTimeRange(start: .zero, duration: both), of: aSource, at: .zero)
        guard let export = AVAssetExportSession(asset: composition, presetName: AVAssetExportPresetPassthrough) else {
            throw Failure(text: "Couldn't save the video.")
        }
        try? FileManager.default.removeItem(at: output)
        try await export.export(to: output, as: .mp4)
        let made = AVURLAsset(url: output)
        if try await made.loadTracks(withMediaType: .audio).isEmpty { throw Failure(text: "The narration didn't make it into the video.") }
    }

    private static func pixelBuffer(_ image: CGImage) -> CVPixelBuffer? {
        var buffer: CVPixelBuffer?
        let attrs = [kCVPixelBufferCGImageCompatibilityKey: true, kCVPixelBufferCGBitmapContextCompatibilityKey: true] as CFDictionary
        CVPixelBufferCreate(kCFAllocatorDefault, Int(size.width), Int(size.height), kCVPixelFormatType_32BGRA, attrs, &buffer)
        guard let buffer else { return nil }
        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        guard let context = CGContext(data: CVPixelBufferGetBaseAddress(buffer), width: Int(size.width), height: Int(size.height),
                                      bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer), space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
        else { return nil }
        context.draw(image, in: CGRect(origin: .zero, size: size))
        return buffer
    }
}
