"""Export the cortex parity fixture for the iPhone Lab (Phase 0, test 1).

WHAT THIS PROVES. The one-cortex plan says a chunk is embedded once, on whichever device
ingested it, and every other device uses that vector as-is. That only holds if the iPhone,
running EmbeddingGemma 2 on the Neural Engine (Core ML, float16), produces the same vectors
as this Mac running it on MLX (bf16). This script writes the Mac's half of that comparison:
real chunks and real questions, formatted EXACTLY as LocalBook embeds them, with the token ids
the Mac's tokenizer produced and the vectors the Mac's production embed path returns. The
iPhone Lab re-embeds the same token ids and compares.

It also writes `cortex_profile.json`: the embedder contract both devices must honour (model,
pooling, prompt templates, dims, precision floor, max tokens). That file becomes the
"embedder key" every synced vector will carry (LB-M1).

READ-ONLY on user data. Reads LanceDB tables and localbook.db (read-only URI), loads the
embedding model in THIS process (about 1.5 GB of unified memory), and writes only its two
output files. It never writes a vector or changes a setting.

Usage (from the repo):
    cd backend && .venv/bin/python scripts/export_cortex_parity.py \
        [--chunks 500] [--queries 50] [--out ~/Desktop/localbook-parity]

Then AirDrop / Finder-copy `parity_fixture.json` to the iPhone and open it in LocalBook Lab.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import random
import sqlite3
import sys
import time
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FORMAT = "localbook-parity-fixture/1"
# The iPhone's Core ML build of EmbeddingGemma 2 (FluidInference/embeddinggemma-2-coreml)
# compiles text functions for 32…512 tokens. Anything longer cannot be reproduced on the
# phone without truncation, so this script counts it rather than hiding it.
PHONE_MAX_TOKENS = 512


# ── small helpers ─────────────────────────────────────────────────────────────
def _cos(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return float("nan")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else float("nan")


def _pct(values: List[float], p: float) -> float:
    s = sorted(v for v in values if not math.isnan(v))
    if not s:
        return float("nan")
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _rid(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def _vec(v) -> List[float]:
    # float32 precision is all either side computes in; 7 significant digits keep the file small.
    return [float(f"{float(x):.7g}") for x in v]


# ── sampling (READ-ONLY) ──────────────────────────────────────────────────────
def sample_rows(n: int, seed: int) -> List[Dict]:
    """Real index rows with their stored vectors, spread across notebooks."""
    import lancedb
    from config import settings

    db_path = str(settings.db_path)
    if not os.path.isdir(db_path):
        sys.exit(f"❌ no LanceDB at {db_path}")
    db = lancedb.connect(db_path)
    # Enumerate directories, not table_names() — see embedding_equivalence.py for why.
    names = sorted(d[: -len(".lance")] for d in os.listdir(db_path)
                   if d.startswith("notebook_") and d.endswith(".lance"))
    rng = random.Random(seed)
    rng.shuffle(names)
    per_table = max(5, n // max(1, min(len(names), 20)))
    out: List[Dict] = []
    for name in names:
        if len(out) >= n:
            break
        try:
            tbl = db.open_table(name)
            if tbl.count_rows() == 0:
                continue
            rows = tbl.to_arrow().to_pylist()
        except Exception as e:  # a damaged table must not stop the export
            print(f"  ⚠️ skipped {name}: {type(e).__name__}: {e}")
            continue
        rows = [r for r in rows if str(r.get("text") or "").strip()]
        rng.shuffle(rows)
        for r in rows[:per_table]:
            out.append({"table": name, "row": r})
            if len(out) >= n:
                break
    return out


def sample_queries(n: int) -> List[str]:
    """Questions the user actually asked (newest first, de-duplicated)."""
    from config import settings
    p = os.path.join(str(settings.data_dir), "localbook.db")
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT query FROM exploration_queries WHERE length(query) > 12 "
            "ORDER BY timestamp DESC LIMIT ?", (n * 4,)).fetchall()
    except Exception as e:
        print(f"  ⚠️ could not read real questions ({e}); the fixture will have none")
        return []
    seen, out = set(), []
    for (q,) in rows:
        k = (q or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(q.strip())
        if len(out) >= n:
            break
    return out


# ── the Mac's side of the comparison ──────────────────────────────────────────
def load_tokenizer(model_id: str, profile):
    """The SAME tokenizer the production loader uses (services/mlx_embed_loaders.load)."""
    from transformers import AutoTokenizer
    if profile.loader == "mlx_vlm":
        from mlx_vlm.utils import get_model_path
        return AutoTokenizer.from_pretrained(str(get_model_path(model_id)))
    from mlx_embeddings import load as _eload
    wrapper = _eload(model_id)[1]
    # mlx-embeddings wraps the HF tokenizer; the wrapper may not forward __call__.
    return getattr(wrapper, "_tokenizer", wrapper)


def embed(texts: List[str], model_id: str) -> List[List[float]]:
    """The production engine path (mlx_engine.embed_sync), called directly so a bad vector
    raises here instead of being zero-filled by the rag_embeddings wrapper."""
    from services.mlx_engine import mlx_engine
    if not mlx_engine.available():
        sys.exit("❌ the MLX engine is not available in this process")
    vecs = mlx_engine.embed_sync(texts, model=model_id)
    if not vecs or len(vecs) != len(texts):
        sys.exit(f"❌ embed returned {len(vecs or [])} vectors for {len(texts)} texts")
    return vecs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--chunks", type=int, default=500)
    ap.add_argument("--queries", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop/localbook-parity"))
    ap.add_argument("--allow-any-model", action="store_true",
                    help="export even if the live embedder is not EmbeddingGemma 2")
    args = ap.parse_args()

    from config import settings
    # The app overlays user_preferences.json onto settings at startup (main.py), so the model
    # you chose in the UI is not the config default. Do the same, or this reports Arctic.
    _prefs = os.path.join(str(settings.data_dir), "user_preferences.json")
    if os.path.exists(_prefs):
        try:
            _m = (json.load(open(_prefs)).get("default_combo") or {}).get("embedding_model")
            if _m:
                settings.embedding_model = _m
        except Exception as e:
            print(f"(could not read {_prefs}: {e})")
    from services import embedding_profile
    from services.embedding_migration import embedded_text
    from services.mlx_engine import _embed_max_length

    model_id = settings.embedding_model
    prof = embedding_profile.for_model(model_id)
    max_len = _embed_max_length(model_id)
    print(f"Embedder: {model_id}  (profile from {prof.source}, loader {prof.loader}, "
          f"pooling {prof.pooling}, max_length {max_len})")
    if "embeddinggemma-2" not in model_id.lower() and not args.allow_any_model:
        sys.exit("❌ the live embedder is not EmbeddingGemma 2 — finish the switch first, "
                 "or pass --allow-any-model")

    tok = load_tokenizer(model_id, prof)

    # 1) chunks, formatted exactly as ingest + the migration embed them
    sampled = sample_rows(args.chunks, args.seed)
    if not sampled:
        sys.exit("❌ no indexed chunks found")
    chunk_texts = [prof.format_document(embedded_text(s["row"])) for s in sampled]

    # 2) questions, formatted exactly as search embeds them
    questions = sample_queries(args.queries)
    query_texts = [prof.format_query(q) for q in questions]

    # 3) token ids as the Mac's tokenizer produces them (production truncates at max_len)
    def ids_of(texts: List[str]):
        full = [tok(t)["input_ids"] for t in texts]
        cut = [tok(t, truncation=True, max_length=max_len)["input_ids"] for t in texts]
        return full, cut

    chunk_full, chunk_ids = ids_of(chunk_texts)
    query_full, query_ids = ids_of(query_texts)
    first = chunk_ids[0] if chunk_ids else []
    bos = getattr(tok, "bos_token_id", None)
    eos = getattr(tok, "eos_token_id", None)

    # 4) the Mac's vectors through the production engine
    t0 = time.time()
    chunk_vecs = embed(chunk_texts, model_id)
    query_vecs = embed(query_texts, model_id) if query_texts else []
    took = time.time() - t0
    for v in chunk_vecs + query_vecs:
        if any(x != x for x in v) or not any(v):
            sys.exit("❌ the Mac produced a NaN or zero vector — stop and look at the embedder")

    # 5) sanity: a fresh vector should equal the one already in the index
    stored_cos = []
    for s, v in zip(sampled, chunk_vecs):
        sv = s["row"].get("vector")
        if sv is not None and len(sv) == len(v):
            stored_cos.append(_cos([float(x) for x in sv], v))

    over = [len(f) for f in chunk_full + query_full if len(f) > PHONE_MAX_TOKENS]
    lengths = [len(f) for f in chunk_full]
    dims = len(chunk_vecs[0])

    profile_doc = {
        "format": "localbook-cortex-profile/1",
        "model_id": model_id,
        "loader_mac": prof.loader,
        "pooling": prof.pooling,
        "query_template": prof.query_template,
        "document_template": prof.document_template,
        "document_title": "none",          # what ingest passes today; see the note in the summary
        "dims": dims,
        "mrl_dims": list(prof.mrl_dims),
        "min_dtype": prof.min_dtype or "bfloat16",
        "max_tokens_mac": max_len,
        "max_tokens_phone": PHONE_MAX_TOKENS,
        "normalize": "l2",
        "bos_token_id": bos,
        "eos_token_id": eos,
        "hyde_join": "{chunk}\n\nQuestions this answers:\n{questions}",
    }

    fixture = {
        "format": FORMAT,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "mac": {
            "model_id": model_id,
            "profile": profile_doc,
            "max_length": max_len,
            "has_bos": bool(first) and first[0] == bos,
            "has_eos": bool(first) and first[-1] == eos,
            "tokenizer_source": model_id,
            "seconds_to_embed": round(took, 2),
        },
        "chunks": [
            {
                "id": _rid(s["table"], str(s["row"].get("chunk_index")), t),
                "table": s["table"],
                "formatted_text": t,
                "token_ids": ids,
                "token_count_full": len(full),
                "vector": _vec(v),
            }
            for s, t, ids, full, v in zip(sampled, chunk_texts, chunk_ids, chunk_full, chunk_vecs)
        ],
        "queries": [
            {
                "id": _rid("q", t),
                "formatted_text": t,
                "token_ids": ids,
                "token_count_full": len(full),
                "vector": _vec(v),
            }
            for t, ids, full, v in zip(query_texts, query_ids, query_full, query_vecs)
        ],
    }

    os.makedirs(args.out, exist_ok=True)
    fx_path = os.path.join(args.out, "parity_fixture.json")
    pf_path = os.path.join(args.out, "cortex_profile.json")
    with open(fx_path, "w") as f:
        json.dump(fixture, f, separators=(",", ":"))
    with open(pf_path, "w") as f:
        json.dump(profile_doc, f, indent=2)

    def line(label, value):
        print(f"  {label:<38}{value}")

    print("\nExported")
    line("chunks / questions", f"{len(fixture['chunks'])} / {len(fixture['queries'])}")
    line("dims", dims)
    line("token ids start with BOS / end with EOS", f"{fixture['mac']['has_bos']} / {fixture['mac']['has_eos']}")
    line("chunk tokens p50 / p95 / max", f"{_pct(lengths, 50):.0f} / {_pct(lengths, 95):.0f} / {max(lengths)}")
    line(f"texts over {PHONE_MAX_TOKENS} tokens (phone cap)", f"{len(over)}" + (f"  ← longest {max(over)}" if over else ""))
    if stored_cos:
        line("fresh vs stored index vector (mean/min)", f"{sum(stored_cos)/len(stored_cos):.6f} / {min(stored_cos):.6f}")
    line("Mac embed time", f"{took:.1f}s")
    print(f"\n  {fx_path}\n  {pf_path}")
    if over:
        print(f"\n⚠️ {len(over)} texts exceed the phone's {PHONE_MAX_TOKENS}-token cap. The Lab reports them "
              "separately; if this is more than a handful, the cortex contract needs max_tokens = 512 "
              "on both devices (a chunker change + re-index) before vectors can be shared.")
    if stored_cos and min(stored_cos) < 0.999:
        print("\n⚠️ Some fresh vectors differ from the ones in your index. That means the index was not "
              "built the way this script formats text — investigate before trusting any parity result.")


if __name__ == "__main__":
    main()
