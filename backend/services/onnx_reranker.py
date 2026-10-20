"""The Mac's reranker at full precision — the same weights the iPhone runs.

FlashRank ships `ms-marco-MiniLM-L-12-v2` as an int8-quantised ONNX file. The iPhone runs the original
full-precision weights on the Neural Engine, and the two rank slightly differently (measured on 600 real
pairs: rank correlation 0.968, same best passage 84%, same top five 93%). Running the same full-precision
model here, through the onnxruntime FlashRank already depends on, removes the difference (0.999 / 100% /
100% against the phone).

Behaves like FlashRank's `Ranker.rerank`: same input and output shapes, same sigmoid scores, same
256-token truncation, so `rag_search.rerank` uses it unchanged. The model file is fetched from Hugging Face
in the background the first time (about 133 MB, official ONNX export of cross-encoder/ms-marco-MiniLM-L-12-v2);
until it is there — or if it cannot be fetched — callers keep using FlashRank, so reranking never waits or breaks.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REPO = "cross-encoder/ms-marco-MiniLM-L-12-v2"
MODEL_FILE = "onnx/model.onnx"
_download_lock = threading.Lock()
_download_started = False


def model_dir() -> Path:
    from config import settings
    return Path(settings.data_dir) / "models" / "reranker-fp32" / "ms-marco-MiniLM-L-12-v2"


def available() -> bool:
    d = model_dir()
    return (d / "model.onnx").exists() and (d / "tokenizer.json").exists()


def fetch_in_background() -> None:
    """Start (once) a background download of the model and tokenizer; never raises."""
    global _download_started
    with _download_lock:
        if _download_started or available():
            return
        _download_started = True
    threading.Thread(target=_fetch, name="reranker-fp32-fetch", daemon=True).start()


def _fetch() -> None:
    global _download_started
    try:
        import shutil
        from huggingface_hub import hf_hub_download
        d = model_dir()
        d.mkdir(parents=True, exist_ok=True)
        for name, dest in ((MODEL_FILE, "model.onnx"), ("tokenizer.json", "tokenizer.json")):
            src = hf_hub_download(REPO, name)
            tmp = d / (dest + ".part")
            shutil.copyfile(src, tmp)
            tmp.replace(d / dest)
        logger.info("[reranker-fp32] model ready in %s", d)
    except Exception as exc:
        logger.warning("[reranker-fp32] could not fetch the full-precision model (%s); keeping FlashRank", exc)
        with _download_lock:
            _download_started = False          # allow a later retry


class FullPrecisionReranker:
    """Drop-in for `flashrank.Ranker.rerank(RerankRequest)`."""

    def __init__(self, directory: Optional[Path] = None, max_length: int = 256):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = Path(directory) if directory else model_dir()
        self.tokenizer = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=max_length)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.session = ort.InferenceSession(str(d / "model.onnx"), providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self.session.get_inputs()}

    def rerank(self, request: Any) -> List[Dict[str, Any]]:
        import numpy as np

        passages = request.passages
        if not passages:
            return []
        enc = self.tokenizer.encode_batch([[request.query, p["text"]] for p in passages])
        feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64)}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.array([e.type_ids for e in enc], dtype=np.int64)
        logits = self.session.run(None, feed)[0].flatten()
        scores = 1.0 / (1.0 + np.exp(-logits))
        for score, passage in zip(scores, passages):
            passage["score"] = score
        passages.sort(key=lambda p: p["score"], reverse=True)
        return passages
