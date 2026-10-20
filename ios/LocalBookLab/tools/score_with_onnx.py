"""Score the rerank test pairs with the full-precision ONNX model, as the Mac's reranker would.
Run with the BACKEND's Python (it has onnxruntime):  backend/.venv/bin/python tools/score_with_onnx.py
Writes tools/cache/onnx_scores.json and prints speed. Tokenisation and sigmoid match FlashRank's."""
import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

cache = Path(__file__).parent / "cache"
model_dir = cache / "ms-marco-MiniLM-L-12-v2-fp32"
tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
tok.enable_truncation(max_length=256)
tok.enable_padding(pad_id=0, pad_token="[PAD]")
sess = ort.InferenceSession(str(model_dir / "model.onnx"), providers=["CPUExecutionProvider"])

out, times = [], []
for item in json.load(open(cache / "rerank_pairs.json")):
    enc = tok.encode_batch([[item["question"], p["text"]] for p in item["passages"]])
    feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
            "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64)}
    t0 = time.perf_counter()
    logits = sess.run(None, feed)[0].flatten()
    times.append(time.perf_counter() - t0)
    out.append((1 / (1 + np.exp(-logits))).tolist())
json.dump(out, open(cache / "onnx_scores.json", "w"))
print(f"{len(out)} questions; median {1000 * float(np.median(times)):.0f} ms per 12 passages on this Mac's CPU")
