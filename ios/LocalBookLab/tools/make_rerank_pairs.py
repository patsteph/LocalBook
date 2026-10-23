"""Build a real rerank test set from the Mac's own index, scored by the Mac's own reranker.

Run with the BACKEND's Python (it has lancedb and flashrank):
    LOCALBOOK_DATA_DIR="$HOME/Library/Application Support/LocalBook" \\
      backend/.venv/bin/python ios/LocalBookLab/tools/make_rerank_pairs.py

For each real question in the parity fixture: pick the notebook whose best chunk matches it most closely,
take that notebook's 12 nearest chunks (the Mac's `retrieval_overcollect`), and score the 12 with the
Mac's FlashRank reranker exactly as `rag_search.rerank` does (ms-marco-MiniLM-L-12-v2, max_length 256).
Read-only on LocalBook data. Writes tools/cache/rerank_pairs.json.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

import lancedb  # noqa: E402
from config import settings  # noqa: E402
from flashrank import Ranker, RerankRequest  # noqa: E402
from services import embedding_migration as em  # noqa: E402

fixture = json.load(open(os.path.expanduser("~/Desktop/localbook-parity/parity_fixture.json")))
db = lancedb.connect(str(em._live_dir()))
tables = {n[len("notebook_"):]: db.open_table(n) for n in em._tables(em._live_dir())}
ranker = Ranker(model_name=settings.reranker_model, cache_dir=str(Path(settings.data_dir) / "models" / "flashrank"),
                max_length=256)

out = []
for q in fixture["queries"]:
    text = q["formatted_text"].split("| query: ", 1)[-1].strip()
    vec = q["vector"]
    best = None
    for nb, t in tables.items():
        try:
            rows = t.search(vec).limit(12).to_list()
        except Exception:
            continue
        rows = [r for r in rows if r["chunk_index"] != -1 or True]
        if rows and (best is None or rows[0]["_distance"] < best[1][0]["_distance"]):
            best = (nb, rows)
    if not best:
        continue
    nb, rows = best
    passages = [{"id": i, "text": r["text"]} for i, r in enumerate(rows)]
    scored = ranker.rerank(RerankRequest(query=text, passages=[dict(p) for p in passages]))
    score_by_id = {p["id"]: float(p["score"]) for p in scored}
    out.append({"question": text, "notebook": nb,
                "passages": [{"text": p["text"], "mac_score": score_by_id[p["id"]]} for p in passages]})
cache = Path(__file__).parent / "cache"
cache.mkdir(exist_ok=True)
(cache / "rerank_pairs.json").write_text(json.dumps(out))
print(f"{len(out)} questions, {sum(len(o['passages']) for o in out)} pairs -> {cache / 'rerank_pairs.json'}")

# Conformance sample for the phone (bundled into the Lab, NOT committed: it contains your note text).
from tokenizers import Tokenizer  # noqa: E402

tok = Tokenizer.from_file(str(Path(settings.data_dir) / "models" / "flashrank" / settings.reranker_model / "tokenizer.json"))
tok.enable_truncation(max_length=256)
tok.enable_padding(length=256, pad_id=0, pad_token="[PAD]")
chosen = [o for o in out if len(o["passages"]) == 12][:3]
conf = {"questions": chosen, "encoding": []}
for c in chosen[:2]:
    for p in c["passages"][:3]:
        e = tok.encode(c["question"], p["text"])
        conf["encoding"].append({"question": c["question"], "passage": p["text"], "ids": e.ids,
                                 "type_ids": e.type_ids, "attention_mask": e.attention_mask})
(cache / "rerank_conformance.json").write_text(json.dumps(conf))
print("conformance sample ->", cache / "rerank_conformance.json")
