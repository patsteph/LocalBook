// swift-tools-version: 6.0
// LocalBookCore: the engine the iPhone app runs, as a package both apps share.
//
// Phase 0 of docs/swift-migration/: skeleton, platform shims and the test target. Phase 1 moves the
// engine files in from ios/LocalBookLab/Sources with no behaviour change (see 01-absorption-map.md).
//
// Two libraries so that the pure parts build and test in a minute on CI without compiling MLX:
//   LocalBookCore     SQLite library, BM25, AnswerRecipe, VisualSpec, sync client, identity, parity harness
//   LocalBookEngines  Gemma (MLX), EmbeddingGemma 2, reranker (Core ML), Kokoro and Parakeet (FluidAudio)
//
// Toolchain fixed by the plan: stable Xcode 27.2, Swift 6.x, macOS 26 and iOS 27 minimum. The pins below are the
// ones the iOS app already builds with (ios/LocalBookLab/project.yml); change them in both places or in neither.
import PackageDescription

let package = Package(
    name: "LocalBookCore",
    platforms: [
        .iOS("27.0"),
        .macOS("26.0"),
    ],
    products: [
        .library(name: "LocalBookCore", targets: ["LocalBookCore"]),
        .library(name: "LocalBookEngines", targets: ["LocalBookEngines"]),
    ],
    dependencies: [
        // 1.7.x needs -enable-experimental-feature BorrowingSequence; keep to the stable line.
        .package(url: "https://github.com/apple/swift-collections", exact: "1.3.0"),
        .package(url: "https://github.com/huggingface/swift-transformers", from: "1.3.5"),
        .package(url: "https://github.com/ml-explore/mlx-swift", "0.31.4"..<"0.32.0"),
        // 3.32.x adds a FoundationModels adapter that does not compile against the iOS 27 SDK the phone app uses;
        // 3.31.4 has Gemma 4 E4B and none of that.
        .package(url: "https://github.com/ml-explore/mlx-swift-lm", exact: "3.31.4"),
        .package(url: "https://github.com/FluidInference/FluidAudio", from: "0.17.6"),
        .package(url: "https://github.com/huggingface/swift-huggingface", from: "0.9.0"),
    ],
    targets: [
        .target(
            name: "LocalBookCore",
            dependencies: [
                .product(name: "Collections", package: "swift-collections"),
            ],
            swiftSettings: [
                .swiftLanguageMode(.v6),
            ]
        ),
        .target(
            name: "LocalBookEngines",
            dependencies: [
                "LocalBookCore",
                .product(name: "MLX", package: "mlx-swift"),
                .product(name: "MLXNN", package: "mlx-swift"),
                .product(name: "MLXLLM", package: "mlx-swift-lm"),
                .product(name: "MLXLMCommon", package: "mlx-swift-lm"),
                .product(name: "MLXHuggingFace", package: "mlx-swift-lm"),
                .product(name: "Tokenizers", package: "swift-transformers"),
                .product(name: "Hub", package: "swift-transformers"),
                .product(name: "HuggingFace", package: "swift-huggingface"),
                .product(name: "FluidAudio", package: "FluidAudio"),
            ],
            swiftSettings: [
                .swiftLanguageMode(.v6),
            ]
        ),
        .testTarget(
            name: "LocalBookCoreTests",
            dependencies: ["LocalBookCore"],
            resources: [
                .copy("Fixtures"),
            ],
            swiftSettings: [
                .swiftLanguageMode(.v6),
            ]
        ),
    ]
)
