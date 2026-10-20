"""Convert a MS MARCO cross-encoder to Core ML and compare it with the Mac's own FlashRank scores.

    tools/.venv/bin/python tools/convert_reranker.py cross-encoder/ms-marco-MiniLM-L-12-v2 L12
    tools/.venv/bin/python tools/convert_reranker.py cross-encoder/ms-marco-MiniLM-L-6-v2  L6

Fixed shape: 12 (query, passage) pairs x 256 tokens, the Mac's `retrieval_overcollect` and FlashRank
`max_length`. Tokenised with the SAME tokenizer.json the Mac's FlashRank uses, so only the model differs.
Writes tools/cache/Reranker<tag>.mlpackage and prints, against the Mac's scores on real pairs: score
correlation, top-1 agreement and top-5 overlap (rerank keep = 5 of 12).
"""
import json
import sys
from pathlib import Path

import coremltools as ct
import numpy as np
import torch
from tokenizers import Tokenizer
from transformers import AutoModelForSequenceClassification

BATCH, SEQ = 12, 256
model_id, tag = sys.argv[1], sys.argv[2]
cache = Path(__file__).parent / "cache"
mac_tok = Path.home() / "Library/Application Support/LocalBook/models/flashrank/ms-marco-MiniLM-L-12-v2/tokenizer.json"

tok = Tokenizer.from_file(str(mac_tok))
tok.enable_truncation(max_length=SEQ)
tok.enable_padding(length=SEQ, pad_id=0, pad_token="[PAD]")

model = AutoModelForSequenceClassification.from_pretrained(model_id, attn_implementation="eager").eval()


class Wrapper(torch.nn.Module):
    """BERT cross-encoder with the mask and position handling written out, so the trace has no dynamic
    integer casts (which coremltools cannot convert)."""

    def __init__(self, m):
        super().__init__()
        self.m = m
        self.register_buffer("positions", torch.arange(SEQ, dtype=torch.long).unsqueeze(0))

    def forward(self, input_ids, attention_mask, token_type_ids):
        bert = self.m.bert
        hidden = bert.embeddings(input_ids=input_ids.long(), token_type_ids=token_type_ids.long(),
                                 position_ids=self.positions)
        additive = (1.0 - attention_mask.to(torch.float32)[:, None, None, :]) * -10000.0
        hidden = bert.encoder(hidden, attention_mask=additive)[0]
        pooled = bert.pooler(hidden) if bert.pooler is not None else hidden[:, 0]
        return self.m.classifier(pooled)


wrapper = Wrapper(model).eval()
example = tuple(torch.zeros((BATCH, SEQ), dtype=torch.int32) for _ in range(3))
traced = torch.jit.trace(wrapper, example)
ml = ct.convert(
    traced,
    inputs=[ct.TensorType(name=n, shape=(BATCH, SEQ), dtype=np.int32) for n in ("input_ids", "attention_mask", "token_type_ids")],
    outputs=[ct.TensorType(name="logits")],
    compute_precision=ct.precision.FLOAT16,
    compute_units=ct.ComputeUnit.CPU_AND_NE,
    minimum_deployment_target=ct.target.iOS18,
)
path = cache / f"Reranker{tag}.mlpackage"
ml.save(str(path))
print("saved", path)

pairs = json.load(open(cache / "rerank_pairs.json"))


def encode(q, passages):
    enc = tok.encode_batch([[q, p] for p in passages])
    ids = np.array([e.ids for e in enc], dtype=np.int32)
    return ids, np.array([e.attention_mask for e in enc], dtype=np.int32), np.array([e.type_ids for e in enc], dtype=np.int32)


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


rows = []
for item in pairs:
    texts = [p["text"] for p in item["passages"]]
    if len(texts) != BATCH:
        continue
    mac = np.array([p["mac_score"] for p in item["passages"]])
    ids, mask, types = encode(item["question"], texts)
    torch_scores = sigmoid(wrapper(*(torch.from_numpy(a) for a in (ids, mask, types))).detach().numpy().flatten())
    cm = ml.predict({"input_ids": ids, "attention_mask": mask, "token_type_ids": types})["logits"].flatten()
    cm_scores = sigmoid(cm)
    top5 = lambda s: set(np.argsort(-s)[:5])
    rows.append({
        "rho_torch": spearman(mac, torch_scores), "rho_coreml": spearman(mac, cm_scores),
        "top1_torch": int(np.argmax(mac) == np.argmax(torch_scores)), "top1_coreml": int(np.argmax(mac) == np.argmax(cm_scores)),
        "top5_torch": len(top5(mac) & top5(torch_scores)) / 5, "top5_coreml": len(top5(mac) & top5(cm_scores)) / 5,
        "max_abs_diff_coreml_vs_torch": float(np.max(np.abs(cm_scores - torch_scores))),
    })
m = lambda k: float(np.mean([r[k] for r in rows]))
print(f"{tag}: {len(rows)} questions x {BATCH} pairs, against the Mac's FlashRank scores")
print(f"  PyTorch fp32 : rank corr {m('rho_torch'):.3f} | same #1 {m('top1_torch'):.0%} | top-5 overlap {m('top5_torch'):.0%}")
print(f"  Core ML fp16 : rank corr {m('rho_coreml'):.3f} | same #1 {m('top1_coreml'):.0%} | top-5 overlap {m('top5_coreml'):.0%}")
print(f"  Core ML vs PyTorch: max score difference {max(r['max_abs_diff_coreml_vs_torch'] for r in rows):.4f}")
