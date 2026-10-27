"""Export the full-precision MS MARCO cross-encoder to ONNX for the Mac's reranker.

    tools/.venv/bin/python tools/export_reranker_onnx.py cross-encoder/ms-marco-MiniLM-L-12-v2 OUT_DIR

The Mac's FlashRank ships an int8-quantised copy of this model; the phone runs the full weights, so the two
rank slightly differently. Running the same full-precision weights on the Mac (through the onnxruntime it
already has) removes that difference. Dynamic batch and sequence; opset 17; same tokenizer files as FlashRank.
"""
import shutil
import sys
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification

model_id, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
model = AutoModelForSequenceClassification.from_pretrained(model_id, attn_implementation="eager").eval()


class Wrapper(torch.nn.Module):
    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, input_ids, attention_mask, token_type_ids):
        return self.m(input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids).logits


ids = torch.randint(1000, 5000, (2, 32), dtype=torch.int64)
args = (ids, torch.ones_like(ids), torch.zeros_like(ids))
torch.onnx.export(
    Wrapper(model).eval(), args, str(out / "model.onnx"), opset_version=17, dynamo=False,
    input_names=["input_ids", "attention_mask", "token_type_ids"], output_names=["logits"],
    dynamic_axes={n: {0: "batch", 1: "sequence"} for n in ("input_ids", "attention_mask", "token_type_ids")} | {"logits": {0: "batch"}},
)
src = Path.home() / "Library/Application Support/LocalBook/models/flashrank/ms-marco-MiniLM-L-12-v2"
for f in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "config.json"):
    shutil.copy(src / f, out / f)
print("exported", out / "model.onnx", f"{(out / 'model.onnx').stat().st_size / 1e6:.0f} MB")
