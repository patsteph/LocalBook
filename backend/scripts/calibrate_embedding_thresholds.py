"""Calibrate a candidate embedder's similarity cut-offs against the live (arctic) ones.

Every cosine threshold in LocalBook was tuned on arctic's similarity scale. A different
model spreads its cosines differently, so the same number means a different decision.
For each feature this script takes the KIND of text that feature compares, from the real
library — READ-ONLY (SQLite opened with mode=ro, LanceDB only read) — embeds it with both
models in that feature's own task format, and picks the candidate threshold whose
match / no-match decisions best agree with arctic's at today's value (F1 on arctic's
matches). A feature with too few arctic matches on this Mac gets a SCALE MAPPING instead:
arctic's threshold carried to the same quantile of the candidate's similarities over every
pair collected (the two models' scales, not that feature's matches). Proxy text is used
where a Mac has none of the real kind, and the report says so.

    backend/.venv/bin/python scripts/calibrate_embedding_thresholds.py \\
        --data "~/Library/Application Support/LocalBook" --out /tmp/thresholds.json [--write-registry]

Nothing is written to the library. `--write-registry` writes the chosen values into the
candidate's `embedding_profile.thresholds` in known_models.json, for review in git.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASELINE = "mlx-community/snowflake-arctic-embed-l-v2.0-bf16"
CANDIDATE = "mlx-community/embeddinggemma-2-bf16"
N = 160                     # texts per symmetric feature → ~12.7k pairs
RNG = random.Random(1015)

# name → (today's arctic value, task for side A, task for side B, source kind)
FEATURES = {
    "router":        (0.75, "query", "document", "router"),
    "consensus":     (0.72, "clustering", "clustering", "passages"),
    "perspectives":  (0.78, "clustering", "clustering", "passages_short"),
    "contradiction": (0.60, "similarity", "similarity", "passages_short"),
    "articles":      (0.65, "clustering", "clustering", "passages"),
    "canvas_assign": (0.58, "clustering", "clustering", "canvas"),
    "canvas_seed":   (0.66, "clustering", "clustering", "canvas"),
    "answer_cache":  (0.92, "query", "query", "questions"),
    "core_memory":   (0.85, "document", "document", "core_memory"),
}
PROXY = {"passages_short": "notebook passages stand in for extracted claims (none stored)",
         "passages": "notebook passages stand in (no articles / event summaries on this Mac)"}


def ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _sample(xs, n):
    xs = [x for x in dict.fromkeys(x.strip() for x in xs if x and x.strip())]
    RNG.shuffle(xs)
    return xs[:n]


def texts_for(kind: str, data: Path):
    db, brain = data / "localbook.db", data / "curator_brain" / "brain.db"
    if kind == "events":
        rows = ro(brain).execute("SELECT payload FROM events WHERE payload IS NOT NULL").fetchall()
        out = []
        for (p,) in rows:
            try:
                d = json.loads(p)
                t = d.get("summary") or d.get("title") or d.get("text") or ""
            except Exception:
                t = ""
            out.append(str(t)[:2000])
        return _sample(out, N), None
    if kind == "canvas":
        rows = ro(db).execute("SELECT title, snapshot_json FROM canvas_nodes").fetchall()
        out = []
        for title, snap in rows:
            try:
                payload = json.loads(snap or "{}").get("payload")
            except Exception:
                payload = None
            out.append(f"{title or ''}\n{payload if isinstance(payload, str) else ''}"[:1500])
        return _sample(out, N), None
    if kind == "questions":
        return _sample([q for (q,) in ro(db).execute("SELECT query FROM exploration_queries")], N), None
    if kind == "core_memory":
        out = []
        for (b,) in ro(db).execute("SELECT body_json FROM documents WHERE kind LIKE 'core%'"):
            try:
                d = json.loads(b)
                out.append(f"{d.get('key', '')}: {d.get('value', '')}" if isinstance(d, dict) else str(d))
            except Exception:
                pass
        return _sample(out, N), None
    if kind == "router":
        digests = [s for (s,) in ro(brain).execute("SELECT current_summary FROM notebook_digests")]
        questions = [q for (q,) in ro(db).execute("SELECT query FROM exploration_queries")]
        return _sample(questions, 120), _sample(digests, 60)
    if kind in ("passages", "passages_short"):
        import os

        import lancedb
        lp = data / "lancedb"
        con = lancedb.connect(str(lp))
        pool = []
        for d in sorted(os.listdir(lp)):
            if d.startswith("notebook_") and d.endswith(".lance"):
                pool += [r["text"] for r in con.open_table(d[:-6]).search().select(["text"]).limit(300).to_list()]
        if kind == "passages_short":     # claim-sized: first sentence-ish of each passage
            pool = [t.split(". ")[0][:300] for t in pool if len(t) > 60]
        return _sample(pool, N), None
    raise ValueError(kind)


def embed(model: str, texts, task: str):
    from services import embedding_profile
    from services.mlx_engine import mlx_engine
    prof = embedding_profile.for_model(model)
    v = np.asarray(mlx_engine.embed_sync([prof.format_task(t, task) for t in texts], model=model), dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def pair_sims(model, a, b, task_a, task_b):
    va = embed(model, a, task_a)
    if b is None:
        s = va @ va.T
        return s[np.triu_indices(len(a), k=1)]
    return (va @ embed(model, b, task_b).T).ravel()


def choose(base: np.ndarray, cand: np.ndarray, base_thr: float):
    pos = base >= base_thr
    grid = np.arange(0.20, 0.995, 0.0025)
    if pos.sum() >= 5:
        best = max(grid, key=lambda t: _f1(pos, cand >= t))
        method = "F1 vs arctic's matches"
    else:                                  # too few matches to learn from: keep the same match RATE
        rate = max(pos.mean(), 1.0 / len(base))
        best = float(np.quantile(cand, 1 - rate))
        method = f"same match rate (only {int(pos.sum())} arctic matches)"
    agree = float(((cand >= best) == pos).mean())
    return round(float(best), 3), method, int(pos.sum()), round(_f1(pos, cand >= best), 3), round(agree, 4)


def _f1(truth, pred):
    tp = float((truth & pred).sum())
    if tp == 0:
        return 0.0
    p, r = tp / max(1.0, pred.sum()), tp / max(1.0, truth.sum())
    return 2 * p * r / (p + r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--candidate", default=CANDIDATE)
    ap.add_argument("--write-registry", action="store_true")
    args = ap.parse_args()
    data = Path(args.data).expanduser()

    from config import settings
    settings.data_dir = Path(tempfile.mkdtemp())      # anything the engine touches: temp, never the library

    report, cache, sims = {}, {}, {}
    for name, (thr, ta, tb, kind) in FEATURES.items():
        if kind not in cache:
            cache[kind] = texts_for(kind, data)
        a, b = cache[kind]
        if len(a) < 8 or (b is not None and len(b) < 3):
            report[name] = {"skipped": f"not enough {kind} on this Mac ({len(a)})"}
            print(f"{name:14s} skipped — not enough {kind}")
            continue
        key = (kind, ta, tb)
        if key not in sims:
            sims[key] = (pair_sims(BASELINE, a, b, ta, tb), pair_sims(args.candidate, a, b, ta, tb))
    # The two models' similarity SCALES, from every pair seen: where a feature has too few
    # arctic matches to learn from, its threshold is mapped across at the same quantile.
    pooled_b = np.concatenate([v[0] for v in sims.values()])
    pooled_c = np.concatenate([v[1] for v in sims.values()])

    for name, (thr, ta, tb, kind) in FEATURES.items():
        if name in report:
            continue
        base, cand = sims[(kind, ta, tb)]
        pos = base >= thr
        if pos.sum() >= 5:
            t, method, npos, f1, agree = choose(base, cand, thr)
        else:
            q = float((pooled_b < thr).mean())
            t = round(float(np.quantile(pooled_c, q)), 3)
            method, npos = f"scale mapping (only {int(pos.sum())} arctic matches here)", int(pos.sum())
            f1, agree = None, round(float(((cand >= t) == pos).mean()), 4)
        report[name] = {"arctic": thr, "candidate": t, "pairs": int(base.size), "arctic_matches": npos,
                        "f1": f1, "agreement": agree, "method": method, "source": kind,
                        **({"proxy": PROXY[kind]} if kind in PROXY else {}),
                        "pair_corr": round(float(np.corrcoef(base, cand)[0, 1]), 3)}
        print(f"{name:14s} arctic {thr:.2f} → {t:.3f}   F1 {f1 if f1 is not None else '—'}  agree {agree:.3%}  "
              f"corr {report[name]['pair_corr']}  ({npos} matches / {base.size} pairs; {method})"
              f"{'  [proxy]' if kind in PROXY else ''}")

    Path(args.out).write_text(json.dumps(report, indent=2))
    if args.write_registry:
        reg = Path(__file__).resolve().parents[1] / "evaluator" / "registry_data" / "known_models.json"
        d = json.loads(reg.read_text())
        for m in d["models"]:
            if m["ollama_name"] == args.candidate:
                m.setdefault("embedding_profile", {})["thresholds"] = {
                    k: v["candidate"] for k, v in report.items() if "candidate" in v}
        reg.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote thresholds into {reg.name}")


if __name__ == "__main__":
    main()
