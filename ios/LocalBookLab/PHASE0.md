# Phase 0 results — what the iPhone can do, and what it should not promise

Device: iPhone18,2 (11.4 GB RAM), iOS 27.2, Xcode 27 beta. Run 2026-10-18 and 2026-10-19 with the
Lab in this folder. Every pass bar was fixed before its numbers existed. Per-test detail is in
`README.md`.

## Scorecard

| # | Question | Result |
|---|---|---|
| 1 | Does the phone make the same vectors as the Mac? | **Pass.** Cosine 0.99993 mean / 0.99981 worst; tokenizer 493/493; best hit the same on 96% of questions; also pass with the phone locked |
| 2 | Can it run the Mac's own answer model (Gemma 4 E4B, 4-bit)? | **Pass.** 15–24 tok/s, first word 0.2–3.7 s (23 to 3,031 prompt tokens), steady over six back-to-back runs, thermal nominal |
| 3 | Can it pair with a Mac and talk to it? | **Pass, with two design changes** (P-256 phone identity; signed requests instead of a client certificate) |
| 4 | Does speech work with the screen locked? | **Pass on every bar** with the Neural Engine + CPU voice engine; GPU engine fails locked |

## What the phone can do on its own

- **Search the synced library, locked or offline.** Embedding matches the Mac; Neural Engine only.
  Cost: ~0.45 GB of memory when resident.
- **Answer questions with the same model as the Mac, screen on.** ~15–18 tok/s sustained, prompts up
  to ~3,000 tokens tested. Cost: 4.2 GB resident, peak 4.8 GB.
- **Read text aloud and transcribe speech**, including with the screen locked. Voice ~11× real time
  in the foreground, ~3× as a background task; transcription 114× / ~32×, 0% word errors.
- **Pair with a Mac** and make signed calls to it.

## Hard constraints we measured (design around these)

1. **Memory.** The app's limit is ~6.1 GB (with the increased-memory entitlement). Gemma + search
   model peak ≈ 5.2 GB at a 3,000-token prompt, leaving ~0.9–1.4 GB for everything else. No
   second large model, no large in-memory index, and keep context short.
2. **No GPU once the screen locks.** iOS refused GPU stages after lock (Kokoro v3: playback stopped
   a second after locking). MLX (Gemma) runs on the GPU, so **do not promise locked-screen answers**.
   **Measured:** Gemma started inside a background task and the screen locked: the lock-screen task
   showed "Task failed" and the app then froze (could not scroll or share; had to be closed). So
   generation must be cancelled the moment the app leaves the foreground (`willResignActive`).
   **Guard verified:** cancelling on `willResignActive` stopped generation 1.3 s in (1 token out) and
   the app stayed responsive afterwards (report shared normally). Caveat: the lock landed early, so a
   lock in the middle of steady decoding is not yet tested.
   Anything that must run locked has to use Neural Engine or CPU only.
3. **Background tasks run ~4× slower** than the foreground (voice 11→2.8×, transcription 114→32×).
   Render short things in the foreground; use a continued-processing task for long batches.
4. **One shared token limit.** The phone embeds up to 512 tokens; the Mac now cuts and splits at the
   same limit (`embedding_profile.max_tokens`), and stored chunks are migrated once at startup.
5. **Apple's TLS cannot use Ed25519 or complete client-certificate auth** in our tests, which is why
   phones use their own P-256 identity and request signatures.
6. **Speed varies run to run** (23–24 tok/s in two runs, 14–18 in another). Plan on the lower number.

## Decisions already made in code

- Shared 512-token limit, splitting at ingest, one-time startup migration of stored chunks.
- Phone identity: P-256 derived from the Mac's `device_identity` seed (Mac side) / Keychain key
  (phone side); phones stored apart from the Macs table; "Paired phones" list with Remove.
- Phone link: pairing code compared on both screens, phone pins the Mac certificate, every request
  signed (ECDSA, no client certificate required). **Hardened (Phase 1 item 1):** the signature covers a
  one-time nonce plus this Mac's id and certificate fingerprint (no replay, no redirect to another Mac),
  expires after 2 minutes, and an address is locked out for 2 minutes after 6 failed attempts in a minute.
- Voice engine: Kokoro legacy (Neural Engine + CPU). Transcription: Parakeet v3.
- Gemma runtime: `mlx-swift-lm` pinned 3.31.4 (3.32.x does not build against the iOS 27 beta SDK).

## Not measured (do not assume)

Battery and energy per task; Gemma answer quality vs the Mac (same weights, so expect equal within
numerics); thermal behaviour beyond ~2 minutes of continuous generation; the model download sizes on
cellular; the cancel-on-lock guard during steady decoding (only an early lock is tested); iPad; production-size libraries
(the work MBP has thousands of items, so check its over-limit chunk count before first sync).

## Before production (security)

Done: nonce, Mac binding, rate limit. Still to do: move the phone key to the Secure Enclave; consider a
PAKE-style pairing instead of a 6-digit code.

## Recommended Phase 1 order

1. ~~Harden the phone link (nonce, binding, rate limit)~~ — done.
2. A phone-side sync client that pulls the library through the phone link (the Mac side has only a
   hello endpoint today) and stores chunk text and the Mac's vectors.
3. On-phone search over those vectors (embedding is already proven), then the answer path with
   Gemma in the foreground, with the memory limits above as acceptance criteria.
4. Read-aloud and voice notes using the proven engines.
5. Only then decide whether locked-screen answering is worth a different (Neural-Engine-only) model.
