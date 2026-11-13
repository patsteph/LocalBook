# LocalBook code audit: what to simplify, and how safely

*Read-only audit of `dev` at `bdee711` (2026-11-06). No code was changed. Everything was measured in a cloud clone; nothing ran on a Mac.*

## The short version

LocalBook is much bigger than it looks from the outside. The Mac backend alone is about **108,000 lines of Python in services**, ~**610 HTTP routes**, and a frontend of ~57,000 lines. The iPhone app is small and tidy by comparison (~11,600 lines of Swift).

The good news is that the parts that matter most (sync, the phone link, LanceDB table listing, the phone's lock handling) are the **best-built and best-tested parts**. The bulk of the weight sits in features that grew by accretion: three generations of visual generators, an Ollama-era LLM layer that now only talks to MLX, and three chat "agents" that are each one giant `if/elif` function.

My recommendation is a **three-wave plan**:

1. **Wave 1 — delete what nothing uses** (~6,000 lines, very low risk, ~95% likely to go cleanly). Pure deletions, proved by grep and by the 2,297 backend tests plus the type check.
2. **Wave 2 — fold duplicate layers into one** (LLM seam, RAG façade, HTTP clients, iOS Studio boilerplate). Medium risk, ~75–85% success, but each needs a few characterization tests written *first*.
3. **Wave 3 — the big consolidations** (visual pipelines, chat agent dispatch). Highest reward, highest risk (~50–65%), and only worth it after Wave 2's tests exist. I would not start these before TestFlight feedback settles.

What I would **not** touch: sync, the phone link, the iOS lock/GPU guard, the retrieval recipe the phone mirrors. They work, they are tested, and simplifying them buys little.

---

## 1. Inventory

| Area | Files | Lines | Notes |
|---|---:|---:|---|
| Backend `services/` | 263 | 108,086 | ~200 single-file services; sync is its own 24-file package |
| Backend `api/` | 73 | 33,792 | ~610 routes across 60 routers |
| Backend `agents/` | 29 | 11,433 | Curator, Collector, Correspondent (mixins + star imports) |
| Backend `storage/` | 31 | 9,305 | SQLite + LanceDB stores |
| Backend `evaluator/` | 40 | 8,813 | Model registry is **imported by production code** (33 imports), not just tests |
| Backend `tests/` | 156 | 28,439 | 2,297 pass, 381 skip (keychain, MLX, etc.) |
| Frontend `src/` | 217 | 56,869 | React 19 + Vite; tsc clean; 6 vitest files, 66 tests |
| Tauri `src-tauri/` | 4 | 1,705 | lib.rs is 1,399 lines |
| Browser extension | ~40 | 4,390 | |
| iOS `ios/LocalBookLab/Sources` | 59 | 11,605 | Two targets: **LocalBook** (product) and **LocalBookLab** (test harness) share most files |

Subsystem weight inside `backend/services` (lines):

| Subsystem | Lines | Subsystem | Lines |
|---|---:|---|---:|
| Visual (visual_*, svg_*, infographic/) | **14,596** | Audio + video generation | 6,889 |
| RAG (rag_*) | 6,041 | Sync | 5,793 |
| Curator brain | 3,927 | LLM + MLX + model_* | 5,318 |
| Canvas | 2,903 | Correspondent | 2,315 |

Churn since 2026-09-01 is concentrated in the iOS `LibraryModel.swift` (38 commits), `backend/main.py` (33), `build_backend.sh` (31) and `services/sync/peer.py` (26). All-time "fix" commits cluster on `main.py`, `build_backend.sh`, `api/settings.py`, `config.py`, `health_portal.py`, `rag_engine.py` and `mlx_engine.py`. Those are where breakage historically lives, so changes there need the most care.

---

## 2. Safety net today

| Net | State | What it protects |
|---|---|---|
| Backend pytest (CI, ubuntu) | **2,297 pass, 381 skip, 0 fail** (re-run here, 2 m 24 s) | Sync, phone link, stores, many services |
| Frontend `tsc --noEmit` (CI) | Clean | Catches dead imports and type breaks from deletions |
| Frontend vitest | 66 pass, **not run in CI** | Only `liveRefresh` / `lockedSignal` and a few libs |
| PyInstaller hidden-import check (CI) | Present | Catches bundle drift from moving modules |
| iOS | **No unit tests, no CI build** | Nothing automatic; only Patrick's device testing |

Coverage by "does any test even import this module": only **~37% of production backend lines** live in modules a test references. The biggest untested modules are exactly the ones that Wave 3 would touch: `audio_generator.py` (2,736), `api/content.py` (2,281), `api/health_portal.py` (2,205), `svg_templates.py` (1,977), `api/visual.py` (1,748), `agents/curator/_brief.py` (1,705), `api/chat/_correspondent.py` (1,656).

(Line coverage with `pytest-cov` could not be measured: with coverage on, the suite runs out of file handles even at a 65k limit. Without coverage it is fine. That points to tests or code that open LanceDB/SQLite handles and never close them; worth a look on its own, see U6.)

---

## 3. The good

- **G1. Sync is well designed.** HLC clocks, a journal, merge rules, scope, retention and blobs are separate small modules (`services/sync/`, most under 300 lines), with 16 dedicated test files. Diff-only is real: the phone pulls a manifest, compares per-notebook digests, then fetches only changed sources (`LibrarySync.swift`). Do not refactor this.
- **G2. The LanceDB 10-table bug is fixed properly** with one helper (`utils/lancedb_tables.py`: `list_tables` pages; `has_table` opens). Every production caller I found uses it (`rag_storage`, `startup_checks`, `api/reindex`, `api/health_portal`, `sync/indexer`). See section 7 for the leftover scripts.
- **G3. The iOS lock handling is careful and centralized.** `GPUGuard.protect` closes the GPU gate, cancels and drains with a timeout; every GPU entry point checks `GPUGuard.canStart` first (Ask, all three Studio paths, soak). This matches the "cancel the instant the app leaves foreground" rule.
- **G4. The iOS product/Lab split is done by file exclusion in `project.yml`,** so the shipping app leaves out the parity harness, speech bench and soak screens. Simple and effective.
- **G5. Frontend hygiene is decent.** `tsc` is clean, only 9 unreachable files, and only 8 exported names with no users. One `api` wrapper handles the locked-volume signal (LB-11) in one place.
- **G6. Comments explain *why*.** The code is full of dated notes on incidents and decisions (e.g. `config.py` on collapsing the Ollama/MLX role pair). That makes a refactor far safer, because intent is recoverable.
- **G7. CI is green and reasonably quick** (~6 minutes), and the keychain tests are opt-in, so the cloud is a safe place to prove refactors.

---

## 4. The bad (real cost, moderate fix)

- **B1. Dead backend code, about 2,700 lines in 156 functions/classes** that nothing references anywhere (not even by string). Largest: `shallow_scrape_remediation.run_shallow_scrape_remediation` (166), `topic_modeling.rebuild_topics` (103), `visual_style_library.build_style_prompt_block` (85), `tabular_store.index_external_db` (85), `web_fallback.augment_rag_response` (70), `context_builder.expand_sources_for_flashcards` (65), `digest_composer.force_ship_now` (63), several in `knowledge_graph.py`, and ~12 thin pass-through methods on `RAGEngine` (`_hybrid_search`, `_load_embedding_model`, `query_stream`, `_chunk_*`, …).
- **B2. About 48 HTTP routes (~960 lines) that no client calls** (frontend, extension, iOS, scripts, other backend code). Examples: the whole `/findings` CRUD, `/audio-llm/tts|voices|speech-to-speech|podcast-audio`, `/curator/judge|synthesize|discover-patterns|devils-advocate`, `/memory/consolidate*`, `/rlm/analyze`, `/timeline/extract-smart`, `/system/engine-truth`, `/visual/analyze`. A few are maintenance endpoints meant for manual `curl` (`/reindex/repair-zero-vectors`, `/reindex/recover-stuck`, `/articles/backfill-all`); keep those, delete the rest.
- **B3. Dead frontend files, ~1,570 lines**, unreachable from `main.tsx`: `VisualPanel.tsx`, `VisualV2Panel.tsx`, `WritingPanel.tsx`, `collector/MorningBrief.tsx`, `canvas/CanvasActionPopover.tsx`, `studio/AudioHistory.tsx`, `studio/ContentViewer.tsx`, `studio/VideoHistory.tsx`, `curator/index.ts`. In `services/visual.ts`, `generateSummary`, `generateMindmap`, `generateFlowchart`, `compareDocuments`, `generateSmart`, `refineVisual`, `waitForCache` and `checkCacheStatus` have no callers, which makes `/visual/summary|mindmap|flowchart|compare|smart|refine` backend routes dead too.
- **B4. Two HTTP styles in the frontend.** The `api` wrapper exists, yet **291 call sites** use raw `localFetch(\`${API_BASE_URL}…\`)` and parse JSON by hand. Each does its own error handling, so the locked-volume and error behaviour is inconsistent.
- **B5. The LLM layer still speaks Ollama.** Since v2.3.0 the only engine is MLX, but there are two seams that both merge registry options, apply the rag profile, add the voice modifier and size `num_ctx`: `llm_service.generate_text/stream_text` (the former `rag_llm.py`) and `llm_runtime.LLMRuntime.generate`. Names and docs still say `ollama_options`, `/api/generate`, `keep_alive`, `_use_ollama_embeddings`. 45 files mention Ollama. It works, but every tuning change must be made twice and the vocabulary misleads readers.
- **B6. Startup has a cosmetic floor.** `main._run_startup_tasks` makes each of 9 status steps last at least 1.2 s so the 1 s frontend poll sees them, which puts a ~11 s floor under background startup completion. Small, easy win, but check the splash screen still looks right.
- **B7. 309 unused imports, 129 f-strings without placeholders, 31 unused locals** (pyflakes), plus ~1,100 `print()` calls next to the logging setup. Pure noise; safe to clean mechanically.
- **B8. Repo clutter:** `backend/scripts/benchmark_output/` (6 MB of PNG/SVG/JSON results committed), `test_fetch.js` at the root, `migrate_data.sh`, an empty `backend/companions` package directory holding one JSON.
- **B9. iOS Studio repeats itself.** `StudioEngine.make`, `makeAudio` and `makeVideo` each repeat the same preamble (busy flag, model ready, `canStart`, idle timer, seed-query retrieval loop, de-dup) and the same "stream with `GPUGuard`" block twice. ~100 lines could become two helpers, and the lock rule would then live in one place for Studio.
- **B10. A Lab-only bench ships in the product app.** `Generation/GemmaBench.swift` (344 lines) is not in the LocalBook exclusion list, so it compiles into the App Store build.

---

## 5. The ugly (structural, expensive to fix)

- **U1. Three visual pipelines live side by side (~14,600 lines).** v1 (`/visual/smart/stream`: `visual_router` + `visual_analyzer` + Mermaid templates), v2 (`/visual/v2/compose`: `visual_composer`, `visual_critic`, skeletons, idioms, slot-fill, style library) and Infographic (`services/infographic/`, 4,344 lines). Both v1 stream and v2 compose are still called from `App.tsx`, `useVisualActions`, `useGenerateVisualToCanvas` and the regenerate buttons. This is the single largest simplification prize and also the riskiest, because almost none of it is tested and output quality is judged by eye.
- **U2. The chat agents are giant functions.** `_stream_correspondent` is one ~1,630-line function with cyclomatic complexity **415**: a ~43-branch `if intent == …` chain. `_stream_collector` (279) and `_stream_curator` (223) follow the same shape. None has a test. A dispatch table of small handlers would be the obvious design, but rewriting untested streaming code is where silent regressions hide.
- **U3. `RAGEngine` is a façade over `rag_search`, `rag_storage`, `rag_embeddings`, `rag_generation`…** with dozens of one-line wrappers, plus its own `query` (CC 56) and `query_stream` (CC 114, and apparently unused). The phone mirrors the Mac's retrieval recipe (`services/sync/phone_recipe.py` ↔ `AnswerRecipe.swift`), so any change here can quietly break answer parity.
- **U4. Agents use star imports and deep mixins.** `from ._models import *` and `from ._common import *` produce ~1,450 "may be undefined" warnings, which means no tool can tell what a curator mixin depends on. The curator is 10 mixin files (5,768 lines) on top of a 16-file `curator_brain` (3,927).
- **U5. Errors are swallowed widely:** ~2,190 `except Exception` blocks in production code, 148 of them followed directly by `pass`. This is why features can fail silently, and it makes a refactor's breakage invisible too. Not something to fix wholesale; fix it in each module a refactor touches.
- **U6. Resource leaks in tests or code.** The full suite exhausts file handles under `pytest-cov`. Likely unclosed LanceDB/SQLite connections or event loops (the run also shows "coroutine was never awaited" and event-loop `__del__` errors). Worth one focused investigation because the same leak in the running Mac app would show up as slow degradation over days.
- **U7. `main.py` does too much at import time** (1,161 lines): encryption migration, staged restore, CLI flags, `.env` purge, SQLite migrations, store singletons, then ~60 `include_router` calls and ~15 schedulers. It is the most-fixed file in the repo. Splitting it is tempting but every step there is order-sensitive (volume must open before any store); leave it alone unless something forces it.
- **U8. The product app's "phone only" rule is enforced by a runtime assignment.** `LibraryModel.askMode` defaults to `.mac`; the product app sets `.phone` in a `.task` at launch. The `askMac()` path and `macAnswer()` (which posts the question to the Mac) are compiled into the App Store build. It works today, but a product rule ("no Ask the Mac mode") should be a compile-time fact, not a default that a future change could skip.

---

## 6. Candidate simplifications: risk, reward and odds

Risk = chance of breaking a user-visible behaviour. Odds = my estimate the change lands with no regression, given the prerequisite tests.

### Wave 1: deletions and noise (do first)

| # | Change | Lines removed | Risk | Reward | Odds | Proof needed before merging |
|---|---|---:|---|---|---:|---|
| 1.1 | Delete the 9 unreachable frontend files and the dead `visualService` methods | ~1,800 | Very low | Medium | 98% | `tsc --noEmit` + vitest + `vite build` |
| 1.2 | Delete truly unreferenced backend functions (B1), one module per commit | ~2,700 | Low | Medium | 95% | Full pytest + compileall + PyInstaller check; grep each name across `backend/`, `src/`, `extension/`, `ios/` and the MCP tool list |
| 1.3 | Delete unused routes (B2, B3), keeping manual maintenance routes | ~1,200 | Low | Medium | 90% | Same, plus check `mcp_server.py` / `mcp_tools_more.py` and `openai_compat` don't call them, and check the extension |
| 1.4 | pyflakes cleanup: unused imports, empty f-strings, unused locals | ~400 | Very low* | Low | 95% | *Careful: some imports exist for side effects or for PyInstaller; keep anything the hidden-import check needs |
| 1.5 | Remove `benchmark_output/`, `test_fetch.js`, stale scripts; move `GemmaBench.swift` into the Lab-only exclusions | 6 MB + ~350 | Very low | Low | 99% | Patrick builds the iOS product scheme once |
| 1.6 | Fix the 4 scripts still calling `db.table_names()` directly (see §7) | ~10 changed | Very low | Low (correctness) | 99% | Run each script against a test DB with >10 tables, in the cloud |

**Wave 1 total: roughly 6,000 lines and 6 MB gone, with no behaviour change.** I'd expect it to take one or two sessions.

### Wave 2: fold duplicates (needs a few tests first)

| # | Change | Risk | Reward | Odds | Tests to write first |
|---|---|---|---|---:|---|
| 2.1 | One LLM seam: make `llm_service.generate_text/stream_text` thin calls into `LLMRuntime`, rename Ollama-era names (`ollama_options` → `gen_options`, etc.), drop `keep_alive`/`think` leftovers | Medium | High (one place to tune models) | 80% | Snapshot tests that, for 6–8 representative callers (chat, RAG, structured JSON, vision OCR, audio script), assert the **final MLX options** (max tokens, temperature, stops, ctx cap, voice prefix) are identical before and after. Pure functions, no model needed |
| 2.2 | Shrink `RAGEngine`: callers use `rag_search`/`rag_storage`/`rag_embeddings` directly; delete wrappers and the unused `query_stream` | Medium | Medium | 85% | Existing RAG tests plus one golden test: fixed LanceDB fixture, fixed embeddings, assert the same top-k and citations. Also re-run the phone parity export to confirm `phone_recipe` output is byte-identical |
| 2.3 | Move the 291 raw `localFetch` calls onto the `api` wrapper, one feature folder per commit | Low–medium | Medium (consistent errors and lock screen) | 85% | Vitest for `apiRequest` (error shape, 503 locked header, FormData) plus a click-through of each changed screen; watch streaming (SSE) calls, which must stay on raw fetch |
| 2.4 | Remove the startup 1.2 s floor (or make it a frontend animation) | Low | Low–medium (faster "ready") | 90% | Watch the splash on one Mac build |
| 2.5 | iOS Studio: extract `gatherPassages(seeds:)` and `streamGuarded(...)` helpers used by make / audio / video | Low–medium | Medium (lock rule in one place) | 85% | No iOS tests exist, so: Patrick makes one of each kind and locks the phone mid-make once. Ideally add a tiny XCTest target for pure helpers first |
| 2.6 | iOS: make phone-only a compile-time fact (product target defines `PRODUCT_APP`; `askMode`/`askMac`/`macAnswer` compile only in the Lab) | Low | Medium (enforces a product rule) | 90% | Build both schemes; ask one question in each |
| 2.7 | Replace star imports in `agents/` with explicit imports | Low | Medium (tools can see dependencies) | 90% | pytest + PyInstaller check; mechanical, guided by pyflakes |

### Wave 3: big consolidations (only after Wave 2 and TestFlight)

| # | Change | Risk | Reward | Odds | Tests to write first |
|---|---|---|---|---:|---|
| 3.1 | Pick **one** visual pipeline (likely v2 compose, plus infographic as a layout of it), route every caller there, delete v1 (`visual_router`, `visual_analyzer`, Mermaid templates, `/visual/smart*`) | **High** | **Very high** (~5–8k lines, one mental model) | 55% | A golden set of ~15 prompts across the 5 layouts with saved outputs, a structural validator (valid SVG, node count, no empty labels), and Patrick's side-by-side review. Also the Canvas and regenerate buttons, which call v2 directly |
| 3.2 | Turn `_stream_correspondent/_collector/_curator` into intent → handler tables, one handler per intent | Medium–high | High (readable, testable) | 65% | Characterization tests first: for each intent, fake the stores and assert the exact SSE events emitted. That is ~90 small tests and most of the work |
| 3.3 | Collapse curator mixins + `curator_brain` into fewer modules | High | Medium | 50% | Morning brief / weekly wrap golden outputs with a stub LLM |
| 3.4 | Split `main.py` startup into an ordered list of named steps | High | Low–medium | 60% | A startup-order test that records which store opens when, plus a locked-volume and staged-restore scenario |

---

## 7. Open items I checked

**Other `table_names()` callers (from the 2026-10-22 fix).** All production code is now safe. Four **scripts** still call the raw API and would see only 10 tables on a library with more notebooks:
`backend/scripts/phone_readiness.py:28`, `upgrade_rag_v3.py:163`, `backfill_missing_questions.py:60`, and `reembed_zero_vectors.py:72` (`db.list_tables()`). `backfill_missing_questions.py` in particular would silently skip notebooks. A one-line switch to `utils.lancedb_tables.list_tables` each (Wave 1.6). There are also three different ways to enumerate tables across the code (`list_tables`, directory walk in `embedding_migration`, `open_table`); fine for now, but worth noting.

**The unexplained iOS relaunch after a settings change.** Not proven, but there is a plausible cause in code. Toggling *Stop safely when the screen locks* calls `reloadAnswerModel()`, which sets `gemma = nil` and state `.idle`, then loads Gemma again. If the toggle is flipped again while that load is running, the guard in `prepareAnswerModel()` (`answerModelState != .loading`) has already been reset to `.idle` by the second `reload`, so **a second full Gemma load starts alongside the first**. Two ~3 GB loads plus the search model would pass the ~6.1 GB limit, and iOS would kill and relaunch the app, which looks like a "relaunch". A single toggle can also briefly hold two copies if a Studio task or the 90-second `producesAnAnswer` check still references the old container. A fix would be to serialize loads (one in-flight load task that later calls await) and confirm with the startup log in `soak-trace.txt`. I'd test on device before changing anything.

---

## 8. Rules any refactor must keep (checklist for reviewers)

- Sync stays diff-only (manifest → digest → changed sources only).
- Search stays scoped to one notebook; only the @curator searches across notebooks.
- The phone answer model loads at app open and stays loaded; never at question time.
- Generation cancels the moment the app leaves the foreground; no GPU work starts from the background.
- Everything in Studio generates on the phone.
- Phone questions stay on the phone (no Ask-the-Mac in the product app).
- Phone retrieval matches the Mac's: any change to RAG must keep `phone_recipe` output identical.
- Never run the full backend test suite or keychain tests on Patrick's Macs; prove refactors in CI or a cloud session.

---

## 9. What I'd skip

- Rewriting sync, the phone link, `GPUGuard`/`StoppableLM`, or encryption/restore startup. They are the most fragile things to get wrong and are already in good shape.
- Wholesale "fix all 2,190 broad excepts". Fix them only inside modules a refactor touches.
- Moving to a new framework or state library on the frontend. The cost is high and nothing in the audit points to it.

---

## Method and limits

- **Dead code:** every production `def`/`class` whose name appears exactly once in the whole backend (so not even referenced by string or `getattr`). Route decorators and MCP tools were excluded. Vulture's raw output (~760 hits) was too noisy to trust on its own because of FastAPI decorators.
- **Unused routes:** all `@router.<verb>` paths, joined with router and `include_router` prefixes, searched for in `src/`, `extension/`, `ios/`, `src-tauri/`, scripts, templates and the rest of the backend. Treat this list as "verify then delete", not "delete".
- **Frontend reachability:** an import graph from `src/main.tsx`, including dynamic `import()`.
- **Complexity:** `radon cc`; 192 functions rated D or worse.
- **Tests:** `pytest tests/` with `requirements-test.txt` (the CI set) on Python 3.13: 2,297 passed, 381 skipped. `npx tsc --noEmit` clean, `vitest` 66/66.
- **Not measured:** iOS (no build tools in the cloud), real line coverage (see U6), runtime performance. Success odds are judgement calls based on test coverage and how much each area has historically broken.
