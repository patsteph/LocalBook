# LocalBook Lab — Phase 0 spike

A bare test harness, not the product. Each screen answers one question that could still
change the iPhone architecture. Tests 1 (embedding parity) and 3 (pairing with a Mac) are wired and passed; tests 2 and 4 come next.

## Test 1: does the iPhone make the same vectors as the Mac?

**Pass bars:** mean similarity ≥ 0.999, worst ≥ 0.998, best hit the same for ≥ 95% of questions
and top-5 overlap ≥ 90% (exact order is float noise at cosine 0.9999), no errors, nothing over
the 512-token limit. Then the same run with the phone locked.

**Result (2026-10-18, iPhone18,2, iOS 27.2):** PASS. Cosine 0.99993 mean / 0.99981 worst,
tokenizer 493/493, retrieval agrees (best hit 96%, top-5 overlap 98.4%). A 3-pass run under the
lock screen embedded 1421 of 1446 items in the background with identical vectors (about 3×
slower). Findings that shaped the product: the Mac's cap had to become a shared 512-token limit
(`embedding_profile.max_tokens`), enforced by splitting long chunks at ingest and by a one-time
startup migration of stored chunks.

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

## Test 3: can the iPhone pair with a Mac?

**Result (2026-10-19):** PASS, with one design change. Bonjour finds the Mac (`_localbook._tcp`);
pairing uses the same 6-digit code as Mac-to-Mac; a signed `/phone/hello` returns the Mac's
models. What the test found:

- **iOS TLS cannot do Ed25519** (handshake fails with -9824 against an Ed25519 certificate, works
  against P-256), so the Mac has a second, phone-only **P-256 identity** (derived from the
  `device_identity` seed). Mac-to-Mac pairing is untouched.
- **Client-certificate auth did not complete on Apple's stack** (URLSession and Network.framework,
  RSA and P-256 alike), so the phone link does not demand one: the phone pins the Mac's
  certificate, and every request carries an ECDSA signature the Mac checks against the key pinned
  at pairing. Production should add a one-time nonce (today: a 5-minute timestamp window).
- Phones live apart from the Macs table (`store.phones()`); they never listen or acknowledge, so
  they must not hold back retention. Settings › Sync lists them under "Paired phones".

Ports: Mac sync `N`, Mac pairing `N+1`, phone pairing `N+2`, phone sync `N+3` (N = 47600).

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
- `Sources/Pairing/*` — Bonjour browser, TLS probe, P-256 phone identity, pairing + signed hello
