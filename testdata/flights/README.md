# Flight test files (flights 1 to 3, 2026-11-27)

Made-up data in the Harbor Lights Coffee universe of `testdata/phase3`; nothing here is real.

## `insights-notebook/` — drop these five files on a new notebook "Harbor Insights"

Five short sources with dates, people and two planted disagreements, so every Insights kind has something to find:

| Planted | Where |
|---|---|
| Employee count **63** (FY23 review) vs **71** (press clipping) | 01 vs 02 |
| Founded **2014** (history) vs **2015** (press clipping) | 01 vs 02 |
| Mountain growth **25 percent by revenue** vs **40 percent by accounts**: compatible, not a contradiction (the clipping muddles them) | 02 vs 04 |
| Dates from 2014 to Q2 2024, people: Rosa Alvarez, Daniel Okafor, Priya Raman, Marcus Lindqvist, Dr. Hana Sato | all |

Expected: the timeline runs 2014 → March 2015 → June 2017 → 2019 → Q4 2021 → Aug 2022 → Feb 2023 → May/June 2023 →
Sept 2023 → Nov 2023 → Jan 2024 → Q1/Q2 2024; the graph has Rosa Alvarez as the most-mentioned node connected to
Harbor Lights Coffee, Portland, Austin, Denver, Daniel Okafor and Priya Raman; contradictions lists the 63/71 and
2014/2015 pairs (the 25/40 pair, if listed, should be low severity or explained as different measures); a comparison
of 01 and 02 puts the employee count and founding year under "They differ on"; perspectives on "company growth" show
both agreed (Austin move, subscription) and contested (headcount) points; topics come out as 3 to 4 groups (history,
shipping, region, wellness).

## `old-app-sample.lbbackup` + `old-app-sample.phrase.txt`

A backup in the old app's format (the CI golden archive, device "golden-device", 2 files). Its recovery phrase is in the
`.phrase.txt` beside it (the BIP-39 test vector, not a real key). In Settings › Security › Open a backup…: this Mac's
key does not open it, the phrase does; it reads 2 files and says it is from the old app and cannot be restored here.

## `scripts/flights_smoke.sh`

One command against the Swift app's local API on this Mac: health, MCP initialize and tools/list, one search_notebook
call, /v1/models and one /v1/chat/completions. Needs an agent key from Settings › Agents (paste when asked). Run it
with the old app quit (both listen on port 8000).
