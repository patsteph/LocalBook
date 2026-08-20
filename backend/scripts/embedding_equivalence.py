"""Embedding equivalence — the gate that decides whether the MLX cutover can proceed.

Stage 0.4 of `READFIRST/planning/mlx-cutover-build-order.md`.

WHY THIS IS THE GATE. Embeddings are the one role never switched over, and the only one that
corrupts PERSISTED data. A slow chat is visible and reversible; an embedding mismatch silently
degrades retrieval across every notebook, and the only fix is re-ingesting everything — if anyone
notices. `config.py` claims MLX bf16 is bit-identical to the stored Ollama vectors (cosine 1.0000)
and 8-bit is ~0.9997, but that claim is an unasserted code comment. This measures it.

⚠️ MUST RUN WHILE OLLAMA IS ALIVE. After the excise this comparison needs a revert and a ~12 GB
re-pull.

READ-ONLY on user data. It reads LanceDB tables and the exploration store, calls embedding
endpoints, and writes ONLY its report file. It never writes a vector, never re-indexes, and never
persists a settings change (engine flips are in-process only and restored in a finally block).

It drives the REAL production path — `rag_embeddings._get_embeddings_batch_sync` with
`settings.embed_engine` flipped — rather than reimplementing embedding. Testing a reimplementation
would prove nothing about what the app actually does.

Usage:
    cd backend && .venv/bin/python scripts/embedding_equivalence.py [--chunks 500] [--queries 50]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BF16 = "mlx-community/snowflake-arctic-embed-l-v2.0-bf16"
INT8 = "mlx-community/snowflake-arctic-embed-l-v2.0-8bit"

# Thresholds from the build order §6. Chosen BEFORE the numbers existed.
GATE = {
    "mean_cosine_min": 0.999,
    "p1_cosine_min": 0.99,
    "recall5_overlap_min": 0.95,
    "top1_changes_max": 0,
    "zero_vectors_max": 0,
    "dim_mismatches_max": 0,
}


# ── vector math (no numpy dependency on the report path) ────────────────────────
def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return float("nan")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return float("nan")
    return dot / (na * nb)


def pct(values: List[float], p: float) -> float:
    """p-th percentile of the SORTED-ASCENDING values (p1 = the bad tail for cosine)."""
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def stats(name: str, values: List[float]) -> Dict:
    clean = [v for v in values if not math.isnan(v)]
    return {
        "name": name,
        "n": len(clean),
        "nan": len(values) - len(clean),
        "mean": sum(clean) / len(clean) if clean else float("nan"),
        "min": min(clean) if clean else float("nan"),
        "p1": pct(clean, 1),
        "p50": pct(clean, 50),
    }


# ── sampling real user data (READ-ONLY) ─────────────────────────────────────────
def sample_chunks(n: int) -> List[Dict]:
    """Real chunks + their STORED vectors, sampled across notebooks."""
    import lancedb
    from config import settings

    db_path = str(settings.db_path)
    if not os.path.isdir(db_path):
        print(f"❌ no LanceDB at {db_path}")
        return []
    db = lancedb.connect(db_path)
    # ⚠️ Do NOT use `table_names()`. On this install it reports 10 tables with 0 rows while
    # `open_table()` on the very same names returns thousands — the listing API under-reports
    # for this on-disk format. Enumerate the directories instead, which is ground truth.
    names = sorted(
        d[: -len(".lance")] for d in os.listdir(db_path)
        if d.startswith("notebook_") and d.endswith(".lance")
    )
    random.shuffle(names)

    out: List[Dict] = []
    per_table = max(5, n // max(1, min(len(names), 20)))
    for tname in names:
        if len(out) >= n:
            break
        try:
            tbl = db.open_table(tname)
            if tbl.count_rows() == 0:
                continue
            rows = tbl.to_pandas()
            if rows.empty or "vector" not in rows.columns or "text" not in rows.columns:
                continue
            take = rows.sample(min(per_table, len(rows)), random_state=42)
            for _, r in take.iterrows():
                txt = str(r.get("text") or "")
                vec = r.get("vector")
                if not txt.strip() or vec is None:
                    continue
                # ⚠️ RECONSTRUCT WHAT WAS ACTUALLY EMBEDDED. `rag_storage.py:190` embeds
                # chunk + the HyDE synthetic questions, NOT the bare `text` column:
                #   f"{chunk}\n\nQuestions this answers:\n{questions}" if questions else chunk
                # Comparing a re-embedding of `text` alone against the stored vector measures
                # that difference, not engine drift — it read as a spurious 0.96 mean until
                # this was corrected.
                q = str(r.get("synthetic_questions") or "").strip()
                embedded = f"{txt}\n\nQuestions this answers:\n{q}" if q else txt
                out.append({
                    "table": tname,
                    "text": embedded,
                    "raw_text": txt,
                    "had_questions": bool(q),
                    "stored": [float(x) for x in list(vec)],
                })
                if len(out) >= n:
                    break
        except Exception as e:
            print(f"  ⚠️ skipped {tname}: {type(e).__name__}: {e}")
    return out


def sample_queries(n: int) -> List[str]:
    """Real questions the user actually asked — better than synthetic probes."""
    try:
        import sqlite3
        from config import settings
        p = os.path.join(str(settings.data_dir), "localbook.db")
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT query FROM exploration_queries WHERE length(query) > 12 "
            "ORDER BY timestamp DESC LIMIT ?", (n * 3,)
        ).fetchall()
        seen, out = set(), []
        for (q,) in rows:
            k = (q or "").strip().lower()
            if k and k not in seen:
                seen.add(k)
                out.append(q.strip())
            if len(out) >= n:
                break
        return out
    except Exception as e:
        print(f"  ⚠️ could not read real queries ({e})")
        return []


# ── the production embedding path, one engine at a time ─────────────────────────
def embed_with(engine: str, model: Optional[str], texts: List[str]) -> Tuple[List[List[float]], float]:
    """Drive the REAL `rag_embeddings` batch path with settings flipped in-process.

    Restores every mutated setting in a finally block — this process shares
    `settings` with nothing else, but a leaked flag would silently mislabel later runs.
    """
    from config import settings
    from services import rag_embeddings

    prev_engine = getattr(settings, "embed_engine", "ollama")
    prev_model = getattr(settings, "mlx_embedding_model", None)
    try:
        settings.embed_engine = engine
        if model:
            settings.mlx_embedding_model = model
        t0 = time.time()
        vecs = rag_embeddings._get_embeddings_batch_sync(texts)
        return vecs, time.time() - t0
    finally:
        settings.embed_engine = prev_engine
        if prev_model is not None:
            settings.mlx_embedding_model = prev_model


def health(vecs: List[List[float]], dim: int) -> Dict:
    """The two silent-corruption modes: zero-filled and wrong-dimension vectors."""
    zeros = sum(1 for v in vecs if not v or not any(v))
    bad_dim = sum(1 for v in vecs if not v or len(v) != dim)
    return {"zero_vectors": zeros, "dim_mismatches": bad_dim, "n": len(vecs)}


# ── retrieval equivalence: the test that actually matters ───────────────────────
def retrieval_check(queries: List[str], chunks: List[Dict], engines: Dict[str, Tuple[str, Optional[str]]]) -> Dict:
    """Rank the SAME stored corpus with query vectors from each engine.

    Cosine similarity between raw vectors can look fine while the retrieved SET changes.
    This measures what the user would actually experience: top-5 overlap and top-1 stability
    against the Ollama baseline, over the corpus already in the index.
    """
    corpus = [c["stored"] for c in chunks]
    results: Dict[str, List[List[int]]] = {}
    for label, (engine, model) in engines.items():
        qv, _ = embed_with(engine, model, queries)
        ranked = []
        for v in qv:
            sims = [(cosine(v, cv), i) for i, cv in enumerate(corpus)]
            sims.sort(key=lambda t: (-t[0] if not math.isnan(t[0]) else 1.0))
            ranked.append([i for _, i in sims[:5]])
        results[label] = ranked

    base = results.get("ollama")
    out: Dict = {"queries": len(queries), "corpus": len(corpus), "vs_ollama": {}}
    if not base:
        return out
    for label, ranked in results.items():
        if label == "ollama":
            continue
        overlaps, top1_changes = [], 0
        for b, r in zip(base, ranked):
            overlaps.append(len(set(b) & set(r)) / 5.0)
            if b and r and b[0] != r[0]:
                top1_changes += 1
        out["vs_ollama"][label] = {
            "mean_top5_overlap": sum(overlaps) / len(overlaps) if overlaps else float("nan"),
            "min_top5_overlap": min(overlaps) if overlaps else float("nan"),
            "top1_changes": top1_changes,
            "top1_change_rate": top1_changes / len(base) if base else float("nan"),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", type=int, default=500)
    ap.add_argument("--queries", type=int, default=50)
    ap.add_argument("--out", default="embedding_equivalence_report.json")
    args = ap.parse_args()

    from config import settings
    dim = settings.embedding_dim

    print("═" * 72)
    print("EMBEDDING EQUIVALENCE — the MLX cutover gate")
    print("═" * 72)
    print(f"dim={dim}  ollama_model={settings.embedding_model}")
    print(f"⚠️  requires a live Ollama at {settings.ollama_base_url}\n")

    print(f"Sampling up to {args.chunks} real chunks from LanceDB…")
    chunks = sample_chunks(args.chunks)
    if len(chunks) < 50:
        print(f"❌ only {len(chunks)} chunks — not enough to gate on. Ingest more, or lower --chunks.")
        return 2
    texts = [c["text"] for c in chunks]
    stored = [c["stored"] for c in chunks]
    print(f"  got {len(chunks)} chunks from {len(set(c['table'] for c in chunks))} notebooks\n")

    engines = {
        "ollama": ("ollama", None),
        "mlx_bf16": ("mlx", BF16),
        "mlx_8bit": ("mlx", INT8),
    }

    report: Dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dim": dim, "chunks": len(chunks), "gate": GATE, "engines": {},
    }

    produced: Dict[str, List[List[float]]] = {}
    for label, (engine, model) in engines.items():
        print(f"Embedding {len(texts)} chunks via {label}…", flush=True)
        try:
            vecs, secs = embed_with(engine, model, texts)
        except Exception as e:
            print(f"  ❌ {label} failed: {type(e).__name__}: {e}")
            report["engines"][label] = {"error": f"{type(e).__name__}: {e}"}
            continue
        produced[label] = vecs
        h = health(vecs, dim)
        h["seconds"] = round(secs, 1)
        h["chunks_per_sec"] = round(len(texts) / secs, 1) if secs > 0 else None
        report["engines"][label] = h
        print(f"  {secs:.1f}s  zero_vectors={h['zero_vectors']}  dim_mismatches={h['dim_mismatches']}")

    print("\nCosine vs the STORED index vectors:")
    report["vs_stored"] = {}
    for label, vecs in produced.items():
        s = stats(label, [cosine(a, b) for a, b in zip(stored, vecs)])
        report["vs_stored"][label] = s
        print(f"  {label:9s} mean={s['mean']:.6f}  p1={s['p1']:.6f}  min={s['min']:.6f}  (n={s['n']}, nan={s['nan']})")

    if "ollama" in produced:
        print("\nCosine vs a FRESH Ollama embedding (isolates model drift from index age):")
        report["vs_fresh_ollama"] = {}
        for label, vecs in produced.items():
            if label == "ollama":
                continue
            s = stats(label, [cosine(a, b) for a, b in zip(produced["ollama"], vecs)])
            report["vs_fresh_ollama"][label] = s
            print(f"  {label:9s} mean={s['mean']:.6f}  p1={s['p1']:.6f}  min={s['min']:.6f}")

    queries = sample_queries(args.queries)
    if queries:
        print(f"\nRetrieval equivalence over {len(queries)} REAL past queries "
              f"(ranking {len(chunks)} stored chunks):")
        report["retrieval"] = retrieval_check(queries, chunks, engines)
        for label, r in report["retrieval"].get("vs_ollama", {}).items():
            print(f"  {label:9s} top5_overlap mean={r['mean_top5_overlap']:.3f} "
                  f"min={r['min_top5_overlap']:.3f}  top1_changes={r['top1_changes']}/{len(queries)}")
    else:
        print("\n⚠️ no real queries available — retrieval check SKIPPED (the gate is incomplete)")

    # ── verdict ────────────────────────────────────────────────────────────────
    print("\n" + "═" * 72)
    print("GATE (thresholds fixed before the numbers existed)")
    print("═" * 72)
    verdict: Dict[str, List[str]] = {}
    for label in ("mlx_bf16", "mlx_8bit"):
        fails: List[str] = []
        h = report["engines"].get(label, {})
        if "error" in h:
            fails.append(f"engine failed: {h['error']}")
        else:
            if h.get("zero_vectors", 0) > GATE["zero_vectors_max"]:
                fails.append(f"{h['zero_vectors']} ZERO vectors (silent index corruption)")
            if h.get("dim_mismatches", 0) > GATE["dim_mismatches_max"]:
                fails.append(f"{h['dim_mismatches']} dim mismatches")
            # GATE ON ENGINE EQUIVALENCE (MLX vs a FRESH Ollama embedding of the same text).
            # `vs_stored` is reported as a DIAGNOSTIC only: an old index can legitimately
            # disagree with today's pipeline (model version, chunking, HyDE questions), and
            # gating on it would fail MLX for something MLX did not cause. The stored-vs-fresh
            # -OLLAMA row is the control — if that is not ~1.0, the INDEX is the problem.
            vs = report.get("vs_fresh_ollama", {}).get(label, {})
            if vs and not math.isnan(vs.get("mean", float("nan"))):
                if vs["mean"] < GATE["mean_cosine_min"]:
                    fails.append(f"mean cosine vs fresh Ollama {vs['mean']:.6f} < {GATE['mean_cosine_min']}")
                if vs["p1"] < GATE["p1_cosine_min"]:
                    fails.append(f"p1 cosine vs fresh Ollama {vs['p1']:.6f} < {GATE['p1_cosine_min']}")
            elif not vs:
                fails.append("no fresh-Ollama baseline — cannot judge engine equivalence")
            r = report.get("retrieval", {}).get("vs_ollama", {}).get(label)
            if r:
                if r["mean_top5_overlap"] < GATE["recall5_overlap_min"]:
                    fails.append(f"top5 overlap {r['mean_top5_overlap']:.3f} < {GATE['recall5_overlap_min']}")
                if r["top1_changes"] > GATE["top1_changes_max"]:
                    fails.append(f"{r['top1_changes']} top-1 changes (limit {GATE['top1_changes_max']})")
            else:
                fails.append("retrieval check did not run — gate incomplete")
        verdict[label] = fails
        print(f"  {label:9s} {'✅ PASS' if not fails else '❌ FAIL'}")
        for f in fails:
            print(f"            · {f}")
    report["verdict"] = {k: ("pass" if not v else "fail") for k, v in verdict.items()}
    report["verdict_detail"] = verdict

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\nreport → {args.out}")
    return 0 if not any(verdict.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
