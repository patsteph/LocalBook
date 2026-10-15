"""How each embedding model must be run — one place to decide it.

Embedders differ in ways that silently wreck retrieval when ignored:

  * **Prompt formats.** Arctic expects `query: ` on queries and nothing on
    documents. EmbeddingGemma expects `task: search result | query: …` and
    `title: none | text: …` — without them its scores drop noticeably. A
    symmetric model is made WORSE by a prefix it never saw in training.
  * **Pooling.** Arctic is CLS-pooled from the last hidden state. EmbeddingGemma
    is mean-pooled through its own dense projection head (`text_embeds`); taking
    token 0 from it yields vectors that are wrong without looking wrong.
  * **Loader.** Arctic loads through mlx-embeddings; EmbeddingGemma 2 through
    mlx-vlm's `embedding_loader`.
  * **Precision.** EmbeddingGemma's activations overflow float16 — bf16 or
    float32 only.

Resolution order: the model registry's `embedding_profile` (authoritative),
then the checkpoint's own `config_sentence_transformers.json` prompts, then the
legacy family prefix table, then plain defaults (CLS, no prompts).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

TEXT = "{text}"

# Family → query prefix, for embedders with no registry profile (moved from rag_embeddings).
# Documents are never prefixed for these, so no existing index goes stale.
_LEGACY_QUERY_PREFIXES = {
    "arctic-embed": "query: ",   # Snowflake Arctic Embed v1 / v2
    "e5-": "query: ",            # intfloat E5 family ("query: " / "passage: ")
    "multilingual-e5": "query: ",
    "bge-": "Represent this sentence for searching relevant passages: ",
    "gte-": "",                  # symmetric — explicitly none
    "nomic-embed": "search_query: ",
}

# Architectures that must be run through their own pooled output and loader.
_MODEL_TYPE_DEFAULTS = {
    "embedding_gemma2": {"loader": "mlx_vlm", "pooling": "model", "min_dtype": "bfloat16"},
    "gemma3_text": {"loader": "mlx_embeddings", "pooling": "model", "min_dtype": "bfloat16"},
}


@dataclass(frozen=True)
class EmbeddingProfile:
    model_id: str
    loader: str = "mlx_embeddings"          # "mlx_embeddings" | "mlx_vlm"
    pooling: str = "cls"                    # "cls" (token 0 of last_hidden_state) | "model" (its own output)
    query_template: str = TEXT
    document_template: str = TEXT
    dims: int = 0
    mrl_dims: Tuple[int, ...] = field(default_factory=tuple)
    min_dtype: str = ""                     # "bfloat16" → refuse a float16 checkpoint
    source: str = "default"                 # where this was resolved from (shown in the Locker)

    @property
    def query_prefix(self) -> str:
        return self.query_template.split(TEXT, 1)[0]

    def format_query(self, text: str) -> str:
        return _fill(self.query_template, text, title=None)

    def format_document(self, text: str, title: Optional[str] = None) -> str:
        return _fill(self.document_template, text, title=title)


def _fill(template: str, text: str, *, title: Optional[str]) -> str:
    if template == TEXT:
        return text
    head = template.split(TEXT, 1)[0].replace("{title}", (title or "none").strip() or "none")
    if text.startswith(head):                # already formatted — never double it
        return text
    return template.replace("{title}", (title or "none").strip() or "none").replace(TEXT, text)


def _from_registry(model_id: str) -> Optional[dict]:
    try:
        from evaluator.model_registry import model_registry
        info = model_registry.get_model(model_id)
    except Exception:
        return None
    if info is None:
        return None
    prof = dict(getattr(info, "embedding_profile", None) or {})
    if not prof:
        return None
    prof.setdefault("dims", getattr(info, "embedding_dim", 0) or 0)
    return prof


def _cached_json(model_id: str, filename: str) -> Optional[dict]:
    """A file from the local HF cache only — never a download."""
    try:
        from huggingface_hub import try_to_load_from_cache
        path = try_to_load_from_cache(model_id, filename)
        if isinstance(path, str):
            with open(path) as f:
                return json.load(f)
    except Exception as exc:
        logger.debug("[embed-profile] %s/%s unreadable: %s", model_id, filename, exc)
    return None


def _from_checkpoint(model_id: str) -> Optional[dict]:
    st = _cached_json(model_id, "config_sentence_transformers.json") or {}
    cfg = _cached_json(model_id, "config.json") or {}
    prompts = st.get("prompts") or {}
    out = dict(_MODEL_TYPE_DEFAULTS.get(str(cfg.get("model_type", "")), {}))
    q = prompts.get("query") or prompts.get("Retrieval-query")
    d = prompts.get("document") or prompts.get("Retrieval-document")
    if q is not None:
        out["query_template"] = q + TEXT
    if d is not None:
        # "title: none | text: " — keep the title slot so a real title can go in.
        out["document_template"] = d.replace("title: none |", "title: {title} |") + TEXT
    return out or None


def _legacy(model_id: str) -> Optional[dict]:
    low = model_id.lower()
    for key, prefix in _LEGACY_QUERY_PREFIXES.items():
        if key in low:
            return {"query_template": prefix + TEXT}
    return None


@lru_cache(maxsize=32)
def for_model(model_id: str) -> EmbeddingProfile:
    model_id = model_id or ""
    for source, fn in (("registry", _from_registry), ("checkpoint", _from_checkpoint), ("family", _legacy)):
        prof = fn(model_id)
        if prof:
            return EmbeddingProfile(
                model_id=model_id,
                loader=prof.get("loader", "mlx_embeddings"),
                pooling=prof.get("pooling", "cls"),
                query_template=prof.get("query_template", TEXT),
                document_template=prof.get("document_template", TEXT),
                dims=int(prof.get("dims", 0) or 0),
                mrl_dims=tuple(int(d) for d in prof.get("mrl_dims", []) or []),
                min_dtype=prof.get("min_dtype", ""),
                source=source,
            )
    return EmbeddingProfile(model_id=model_id)


def active() -> EmbeddingProfile:
    try:
        from config import settings
        return for_model(getattr(settings, "embedding_model", "") or "")
    except Exception:
        return EmbeddingProfile(model_id="")


def format_queries(texts: List[str], profile: Optional[EmbeddingProfile] = None) -> List[str]:
    p = profile or active()
    return [p.format_query(t) for t in texts]


def format_documents(texts: List[str], profile: Optional[EmbeddingProfile] = None,
                     titles: Optional[List[Optional[str]]] = None) -> List[str]:
    p = profile or active()
    if p.document_template == TEXT:
        return texts
    titles = titles or [None] * len(texts)
    return [p.format_document(t, title) for t, title in zip(texts, titles)]
