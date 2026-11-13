# LocalBookCore

The engine LocalBook runs on the iPhone, as a Swift package both apps share. Part of the native Swift Mac app
plan in `docs/swift-migration/`.

| Library | Holds | Depends on |
|---|---|---|
| `LocalBookCore` | SQLite library, BM25, `AnswerRecipe`, `VisualSpec`, sync client, identity, parity harness, platform shims | swift-collections |
| `LocalBookEngines` | Gemma (MLX), EmbeddingGemma 2, Core ML reranker, Kokoro and Parakeet | mlx-swift, mlx-swift-lm, FluidAudio, swift-transformers |

Platforms: iOS 27 and macOS 26. Toolchain: stable Xcode 27.2.

```bash
cd swift/LocalBookCore
swift build --build-system native      # Xcode 27's new build system misbehaves on some dependencies
swift test  --build-system native
```

Rules:
- Nothing in the package imports `UIKit` or `AppKit` outside `Sources/LocalBookCore/Platform/`.
- Phase 1 moves files from `ios/LocalBookLab/Sources` here with no behaviour change; the iOS app's on-disk
  layout (`AppFolders`) does not move.
- Dependency pins match `ios/LocalBookLab/project.yml`; change both or neither.
