"""Friendly model display names — ONE place so LLM Labs, the Evaluator, and the
menu-bar tray all show the same short human name (user directive: friendly names
EVERYWHERE, never the raw 'mlx-community/gemma-4-e4b-it-4bit' path).
"""
from __future__ import annotations

import re


def _prettify(text: str) -> str:
    words = []
    for w in text.replace("-", " ").replace("_", " ").split():
        # keep short model tokens like e4b / 2b / 4 as-is; capitalize real words
        words.append(w if re.match(r'^[a-z]?\d', w) else w.capitalize())
    return " ".join(words).strip()


def friendly_model_name(model_id: str) -> str:
    """'mlx-community/gemma-4-e4b-it-4bit' → 'Gemma 4 e4b (MLX)';
    'gemma4:e4b' → registry display_name ('Gemma 4 e4b') or a prettified fallback;
    '' → ''."""
    if not model_id:
        return ""
    # MLX / HuggingFace path form
    if "/" in model_id:
        base = model_id.split("/")[-1]
        base = re.sub(r'-(4bit|8bit|bf16|fp16|q4|q8|q4_k_m|q8_0)$', '', base, flags=re.I)
        base = re.sub(r'-(it|instruct|chat)$', '', base, flags=re.I)
        return (_prettify(base) + " (MLX)").strip()
    # Ollama "family:tag" — prefer the registry's curated display name
    try:
        from evaluator.model_registry import model_registry
        info = model_registry.get_model(model_id)
        if info and getattr(info, "display_name", None):
            return info.display_name
    except Exception:
        pass
    base = model_id.split(":")[0]
    tag = model_id.split(":", 1)[1] if ":" in model_id else ""
    name = _prettify(base)
    if tag and tag != "latest":
        name += f" {tag}"
    return name
