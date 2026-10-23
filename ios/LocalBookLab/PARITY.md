# Mac / iPhone answer parity — final state (2026-10-20)

Goal: the same question on the Mac and on the phone should be answered from the same passages, by the
same rules, so the answer can be trusted equally. Phone questions stay phone-only.

## How alignment works

The Mac publishes its **recipe** (`/phone/library/recipe`): prompts, question classifier, decode settings,
retrieval numbers, query-expansion tables, citation rules, cleaning steps, routing/format/entity rules,
quality-check and retry rules — plus **conformance vectors** computed by the Mac's own functions.
The phone re-runs its own code on those vectors at every sync and reports any mismatch (Library screen).
Rules are data, so a change on the Mac reaches the phone at the next sync; the proof guards against drift.

| Stage | Mac | Phone |
|---|---|---|
| Query expansion | `rag_query_analyzer.expand_query` | same tables, from the recipe |
| Search | `hybrid_search`: vector + BM25Okapi, RRF k=60, pool 24 | exact BM25Okapi port + RRF over Mac's vectors (Accelerate) |
| Rerank | MiniLM-L-12-v2, **full precision** (`reranker_precision="fp32"`) | same weights, Core ML (Neural Engine) |
| Re-order | entity boost (≤ +0.3), source-type routing | same rules, entities/relationships synced per notebook |
| Citations | `rag_context.citation_confidence` (max of rerank- and RRF-based), keep ≥ 0.20 | same, from the recipe |
| Prompt | profile + core memory + type prompt + format hint; entity/graph context after passages | same text, synced |
| Output | cleaning steps; `fix_citations` drops numbers that point at no source | same; plus trimmed to last finished sentence if cut off |
| Second look | weak answer → query variants → retrieve → answer again | same rules |

Not ported (need the Mac's large model or change per question): the AI query analysis, sub-question
splitting, archival-memory search / recent turns, community "topic overview".

## Measured (10 questions, same fixture)

| | Baseline | Final |
|---|---|---|
| Answer similarity | 0.879 | ~0.90–0.91 |
| Source overlap | 73% | 87% |
| Passage overlap | 61% | 79% |
| Same best source | 70% | 80% |
| Same best passage | 60% | 70% |

The remaining differences are all in the live Mac's AI query-analysis step: stage-by-stage runs of the
Mac's rule-based pipeline give the phone's exact passages on the three questions that differed.

## Defects found along the way (Mac)

* `citation_confidence` had a cliff: a rerank score just above 0.01 *replaced* the RRF value, so the best
  passage could score 0.14 and be cut at 0.20 while weaker ones (rerank < 0.01) kept 0.5–0.9. Fixed 2026-10-20
  (takes the higher of the two); regression test in `backend/tests/test_phone_recipe.py`.
* Model answers could cite numbers that point at no source (`[562]`, `[16.7.3]`); now stripped on both sides.
* The reranker "loaded" message went to stdout, never `backend.log`; now a `logging` line.

## Re-running the test

1. Mac: `./build.sh --rebuild` (check `backend.log` for "Reranker: full precision").
2. Lab: rebuild; Library → Sync (recipe line must say all checks pass); "Get search model ready".
3. Mac vs phone → run the saved 10 questions → share the JSON report.

Tools: `ios/LocalBookLab/tools/` (reranker conversion/export/comparison). Diagnostics that read the real
notebooks are deliberately not committed.
