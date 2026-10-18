# LocalBook Lab — Phase 0 spike

A bare test harness, not the product. Each screen answers one question that could still
change the iPhone architecture. Test 1 (embedding parity) is wired; tests 2–4 come next.

## Test 1: does the iPhone make the same vectors as the Mac?

**Pass bars (fixed before any numbers):** mean similarity ≥ 0.999, worst ≥ 0.998, the same
top-5 chunks for every question, no errors. Then the same run with the phone locked.

### 1. On the Mac: export the fixture

```bash
cd ~/LocalBook/backend
.venv/bin/python scripts/export_cortex_parity.py --chunks 500 --queries 50
```

Writes `~/Desktop/localbook-parity/parity_fixture.json` and `cortex_profile.json`, and prints
chunk token lengths. **Read the "over 512 tokens" line**: the phone model's hard cap is 512.

### 2. Build the Lab

```bash
brew install xcodegen          # once
cd ios/LocalBookLab
xcodegen generate
open LocalBookLab.xcodeproj
```

In Xcode: pick your team under Signing & Capabilities, choose your iPhone, Run.
Xcode 27 and iOS 27 are required (Background Inference entitlement, Core ML function names).

If signing complains about **Background Inference** or **Increased Memory Limit**, enable
them for the App ID in the developer portal (Certificates, Identifiers & Profiles). If the
inference one can't be enabled, note it: that answers the "does Apple gate it" question.

### 3. On the iPhone

1. AirDrop `parity_fixture.json` to the phone and open it in LocalBook Lab, or copy it into
   the app from Finder (iPhone › Files › LocalBook Lab). The Lab picks it up automatically.
2. **Download and prepare** — about 530 MB on Wi-Fi, then a one-time Neural Engine compile
   (several minutes; the warm-up time is reported).
3. **Run now.** Read the verdict.
4. Set passes to 3 or more, **Run in background, then lock the phone.** Unlock when the
   system progress finishes. "While in background" should be most of the items.
5. **Share report** and send the JSON back (it's also left in the app's Documents folder).

If the Neural Engine run fails, switch Compute to GPU or CPU and run again — that tells us
whether the problem is the Neural Engine path or the vectors themselves.

## What it measures

| Check | How |
| --- | --- |
| Runtime parity | The phone embeds the Mac's own token ids, so differences are the runtime alone |
| Tokenizer parity | The phone tokenises the same strings and compares ids one by one |
| Retrieval parity | Both sides rank the same chunks for each question; top-5 and top-1 compared |
| Background | Items finished while the app was in the background, under a continued-processing task |
| Speed | Median milliseconds per token on the chosen compute unit |

## Files

- `Sources/Embedding/EmbeddingModelStore.swift` — download + compile, kept in Application Support
- `Sources/Embedding/EmbeddingGemma2Engine.swift` — token ids → vector on the Neural Engine (becomes the real embedder)
- `Sources/Parity/*` — fixture, runner, background task, screen
