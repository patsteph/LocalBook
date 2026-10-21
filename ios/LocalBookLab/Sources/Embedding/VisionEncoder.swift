import CoreGraphics
import CoreML
import Foundation

/// EmbeddingGemma 2's image encoder (Core ML, on the GPU): a picture becomes image tokens in the text model's own
/// space, and the text model turns `<bos> <|image> tokens <image|> <eos>` into the same 768-number vector a
/// sentence gets, so a typed question can find a picture.
///
/// Driven as the model card describes: resize keeping the aspect ratio to sides that are multiples of 48 px with
/// at most 9 × tokens patches of 16 px; RGB scaled to [0, 1]; row-major 16×16×3 patches; padded to the function's
/// patch count; 3×3 pooling into tokens.
actor VisionEncoder {
    static let tokens = 140                        // the middle budget: 88% on the card's pet test at 34 ms
    static let patchSide = 16
    static let group = 3
    static let boi = 255_999
    static let eoi = 258_882
    private static let patchCount = tokens * 9    // 1,260
    private static let sideUnit = patchSide * group // 48

    private let model: MLModel
    private let table: Data                        // [2][1024][768] fp16
    private let engine: EmbeddingGemma2Engine

    init(location: EmbeddingModelStore.VisionLocation, engine: EmbeddingGemma2Engine) throws {
        let config = MLModelConfiguration()
        config.computeUnits = .cpuAndGPU
        config.functionName = "vision_\(Self.tokens)"
        model = try MLModel(contentsOf: location.compiledModel, configuration: config)
        table = try Data(contentsOf: location.positions, options: .alwaysMapped)
        guard table.count == 2 * 1024 * 768 * 2 else { throw LabError.model("position_embeddings.f16 has an unexpected size") }
        self.engine = engine
    }

    /// The target size for an image: aspect kept, sides multiples of 48, no more than 1,260 patches.
    static func targetSize(width: Int, height: Int) -> (w: Int, h: Int) {
        let scale = (Double(patchCount * patchSide * patchSide) / Double(max(1, width * height))).squareRoot()
        func side(_ v: Int) -> Int { max(sideUnit, Int((Double(v) * scale / Double(sideUnit)).rounded(.down)) * sideUnit) }
        var w = side(width), h = side(height)
        while (w / patchSide) * (h / patchSide) > patchCount {            // a very thin image: shrink the long side
            if w >= h { w -= sideUnit } else { h -= sideUnit }
        }
        return (max(w, sideUnit), max(h, sideUnit))
    }

    /// RGB bytes of `image` drawn at `w` × `h`.
    private static func pixels(_ image: CGImage, w: Int, h: Int) throws -> [UInt8] {
        var buf = [UInt8](repeating: 0, count: w * h * 4)
        let ok = buf.withUnsafeMutableBytes { raw -> Bool in
            guard let ctx = CGContext(data: raw.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                      space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return false }
            ctx.interpolationQuality = .high
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
            return true
        }
        guard ok else { throw LabError.model("could not read the picture's pixels") }
        return buf
    }

    private func array(_ name: String, shape: [Int]) throws -> MLMultiArray {
        let type = model.modelDescription.inputDescriptionsByName[name]?.multiArrayConstraint?.dataType ?? .float16
        return try MLMultiArray(shape: shape.map { NSNumber(value: $0) }, dataType: type)
    }

    /// Writes straight into an array's memory, honouring its strides and element type (fast; subscripting with
    /// NSNumbers would cost seconds for a picture's two million values).
    private struct Cells {
        let base: UnsafeMutableRawPointer
        let strides: [Int]
        let half: Bool
        init(_ a: MLMultiArray) {
            base = a.dataPointer
            strides = a.strides.map(\.intValue)
            half = a.dataType == .float16
        }
        func set(_ i: Int, _ j: Int, _ k: Int, _ v: Float) {
            let off = i * strides[0] + j * strides[1] + (strides.count > 2 ? k * strides[2] : 0)
            if half { base.assumingMemoryBound(to: Float16.self)[off] = Float16(v) }
            else { base.assumingMemoryBound(to: Float.self)[off] = v }
        }
    }

    private func predict(_ input: MLDictionaryFeatureProvider) throws -> MLFeatureProvider { try model.prediction(from: input) }

    /// A unit 768-d vector for the picture.
    func embed(_ image: CGImage) async throws -> [Float] {
        let (w, h) = Self.targetSize(width: image.width, height: image.height)
        let rgba = try Self.pixels(image, w: w, h: h)
        let gw = w / Self.patchSide, gh = h / Self.patchSide
        let n = Self.patchCount, patches = gw * gh
        let realTokens = (gw / Self.group) * (gh / Self.group)
        guard patches <= n, realTokens > 0 else { throw LabError.model("the picture is too large to read") }

        let patchArr = try array("patches", shape: [1, n, 768])
        let posArr = try array("positions", shape: [1, n, 2])
        let posEmb = try array("position_embeddings", shape: [1, n, 768])
        let valid = try array("valid", shape: [1, n])
        let pool = try array("pool", shape: [Self.tokens, n])

        for a in [patchArr, posArr, posEmb, valid, pool] { memset(a.dataPointer, 0, a.shape[0].intValue * a.strides[0].intValue * (a.dataType == .float16 ? 2 : 4)) }
        let patchC = Cells(patchArr), posC = Cells(posArr), embC = Cells(posEmb), validC = Cells(valid), poolC = Cells(pool)
        for p in patches..<n { posC.set(0, p, 0, -1); posC.set(0, p, 1, -1) }      // padding: position −1, all else zero
        table.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            for p in 0..<patches {
                let gx = p % gw, gy = p / gw
                posC.set(0, p, 0, Float(gx)); posC.set(0, p, 1, Float(gy))
                validC.set(0, p, 0, 1)
                for r in 0..<Self.patchSide {                                       // pixels: [16 rows][16 cols][RGB]
                    for c in 0..<Self.patchSide {
                        let src = ((gy * Self.patchSide + r) * w + (gx * Self.patchSide + c)) * 4
                        let base = (r * Self.patchSide + c) * 3
                        for ch in 0..<3 { patchC.set(0, p, base + ch, Float(rgba[src + ch]) / 255) }
                    }
                }
                for d in 0..<768 {                                                  // table[0][x] + table[1][y]
                    let xBits = raw.loadUnaligned(fromByteOffset: (gx * 768 + d) * 2, as: UInt16.self)
                    let yBits = raw.loadUnaligned(fromByteOffset: ((1024 + gy) * 768 + d) * 2, as: UInt16.self)
                    embC.set(0, p, d, Float(Float16(bitPattern: UInt16(littleEndian: xBits))) + Float(Float16(bitPattern: UInt16(littleEndian: yBits))))
                }
                let token = (gy / Self.group) * (gw / Self.group) + (gx / Self.group)   // 3×3 group → pooled token
                if token < realTokens { poolC.set(token, p, 0, 1.0 / 9.0) }
            }
        }

        let input = try MLDictionaryFeatureProvider(dictionary: [
            "patches": MLFeatureValue(multiArray: patchArr), "positions": MLFeatureValue(multiArray: posArr),
            "position_embeddings": MLFeatureValue(multiArray: posEmb), "valid": MLFeatureValue(multiArray: valid),
            "pool": MLFeatureValue(multiArray: pool),
        ])
        let out = try predict(input)
        guard let tokensOut = out.featureValue(for: "image_tokens")?.multiArrayValue else {
            throw LabError.model("the image model returned no image_tokens")
        }
        var entries: [EmbeddingGemma2Engine.Entry] = [.token(2), .vector(try await engine.tokenVector(Self.boi))]
        for t in 0..<realTokens {
            var row = [Float16](repeating: 0, count: 512)
            for d in 0..<512 { row[d] = Float16(tokensOut[[0, t, d] as [NSNumber]].floatValue) }
            entries.append(.vector(row))
        }
        entries.append(.vector(try await engine.tokenVector(Self.eoi)))
        entries.append(.token(1))
        return try await engine.embed(entries: entries)
    }
}
