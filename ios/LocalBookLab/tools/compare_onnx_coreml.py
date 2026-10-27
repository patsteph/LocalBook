"""Compare the Mac-side full-precision ONNX scores with the old Mac scores and the phone's Core ML scores.
    tools/.venv/bin/python tools/compare_onnx_coreml.py"""
import json
from pathlib import Path

import coremltools as ct
import numpy as np
from tokenizers import Tokenizer

cache = Path(__file__).parent / "cache"
pairs = json.load(open(cache / "rerank_pairs.json"))
onnx = json.load(open(cache / "onnx_scores.json"))
tok = Tokenizer.from_file(str(cache / "ms-marco-MiniLM-L-12-v2-fp32" / "tokenizer.json"))
tok.enable_truncation(max_length=256)
tok.enable_padding(length=256, pad_id=0, pad_token="[PAD]")
ml = ct.models.MLModel(str(cache / "RerankerL12.mlpackage"), compute_units=ct.ComputeUnit.CPU_AND_NE)


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


rows = []
for item, o in zip(pairs, onnx):
    if len(item["passages"]) != 12:
        continue
    old = np.array([p["mac_score"] for p in item["passages"]]); new = np.array(o)
    enc = tok.encode_batch([[item["question"], p["text"]] for p in item["passages"]])
    feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int32),
            "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int32),
            "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int32)}
    phone = 1 / (1 + np.exp(-ml.predict(feed)["logits"].flatten()))
    top5 = lambda s: set(np.argsort(-s)[:5])
    rows.append({"rho_old_vs_phone": spearman(old, phone), "rho_new_vs_phone": spearman(new, phone),
                 "t1_old": int(np.argmax(old) == np.argmax(phone)), "t1_new": int(np.argmax(new) == np.argmax(phone)),
                 "t5_old": len(top5(old) & top5(phone)) / 5, "t5_new": len(top5(new) & top5(phone)) / 5,
                 "maxdiff_new": float(np.max(np.abs(new - phone)))})
m = lambda k: float(np.mean([r[k] for r in rows]))
print(f"{len(rows)} questions x 12 pairs: phone (Core ML, half precision) vs the Mac's reranker")
print(f"  Mac int8 (today)       : rank corr {m('rho_old_vs_phone'):.3f} | same #1 {m('t1_old'):.0%} | same top 5 {m('t5_old'):.0%}")
print(f"  Mac full precision ONNX: rank corr {m('rho_new_vs_phone'):.3f} | same #1 {m('t1_new'):.0%} | same top 5 {m('t5_new'):.0%}")
print(f"  largest single-score difference (new Mac vs phone): {max(r['maxdiff_new'] for r in rows):.4f}")
