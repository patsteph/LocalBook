"""
Voice Modifier — per-model-family tone / style instructions.

Different model families have different default voices. olmo writes in a
journalistic register; Gemma 4 has a tendency toward verbose hedging
("it is worth noting", "in the context of"); Phi tends toward markdown
maximalism. To keep agent and chat output consistent across model swaps,
every system prompt gets a short voice-instruction prefix from this
module.

Design rules:
  - Modifiers are SHORT (2-3 sentences max). Long modifiers fight the
    actual instruction in the system prompt.
  - Modifiers describe TONE only — never content, never structure.
  - Skipping is safe — an unknown family returns an empty string.
  - Skipping is opt-in — callers that produce structured JSON / SVG /
    Mermaid pass voice_modifier=False to avoid contaminating format-
    sensitive output with prose-tone instructions.

Wired in:
  - services/ollama_service.generate / chat (every agent call)
  - services/rag_llm.call_ollama / stream_ollama (RAG + content gen)

Family modifiers are keyed by registry `family` field, so adding a new
family is a one-line addition here.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Per-family voice instructions. Keep each one ≤ 3 short sentences.
# The "default" key is used when a family has no specific entry — it's
# the universal "be terse" instruction that fits any well-aligned model.
_FAMILY_VOICE: dict[str, str] = {
    "olmo": (
        "Write in concise, journalistic prose. "
        "Lead with the conclusion. Skip preamble and meta-commentary."
    ),
    "gemma": (
        "Write tightly. Skip preamble — start with the substance. "
        "Avoid hedging phrases like 'it is worth noting', 'in the context of', "
        "or 'fundamentally'. Use bold and italics sparingly."
    ),
    "phi": (
        "Be direct. Avoid filler words and over-formatted markdown. "
        "Match the response length to the question's depth."
    ),
    "llama": (
        "Write clearly and directly. Avoid unnecessary preamble or sign-off."
    ),
    "mistral": (
        "Write directly. Skip apologies and meta-commentary."
    ),
    "granite": (
        "Write clearly and concisely. Skip preamble; lead with substance."
    ),
    "default": (
        "Write directly and concisely. Skip preamble."
    ),
}


def _family_for_model(model_name: str) -> str:
    """Resolve a model name to its registered family, or 'default'."""
    if not model_name:
        return "default"
    try:
        from evaluator.model_registry import model_registry
        info = model_registry.get_model(model_name)
        if info and info.family:
            return info.family.lower()
    except Exception as _e:
        logger.debug(f"[voice-modifier] family lookup failed: {_e}")
    # Fallback: pull family from the name prefix (e.g. "gemma4:e4b" → "gemma")
    base = model_name.split(":")[0].lower()
    for fam in ("olmo", "gemma", "phi", "llama", "mistral", "granite", "qwen", "bonsai"):
        if base.startswith(fam):
            return fam
    return "default"


def get_voice_modifier(model_name: Optional[str] = None) -> str:
    """Return the voice instruction string for the given model.

    Resolves the family from the registry and returns the matching
    modifier. Falls back to a universal "be terse" default for unknown
    families. Returns empty string if disabled or model is None.
    """
    if not model_name:
        # Use the active main model's family by default.
        try:
            from config import settings
            model_name = settings.ollama_model
        except Exception:
            return _FAMILY_VOICE["default"]
    family = _family_for_model(model_name)
    return _FAMILY_VOICE.get(family, _FAMILY_VOICE["default"])


def voiced_system(
    base_system: Optional[str],
    model_name: Optional[str] = None,
    enabled: bool = True,
) -> Optional[str]:
    """Prepend the active model's voice modifier to a system prompt.

    Pass-through if enabled=False or base_system is None/empty.
    Avoids double-injection: if base_system already starts with the
    voice modifier (e.g. caller already wrapped), returns base_system
    unchanged.
    """
    if not enabled or not base_system:
        return base_system
    voice = get_voice_modifier(model_name)
    if not voice:
        return base_system
    if base_system.lstrip().startswith(voice):
        return base_system  # already voiced
    return f"{voice}\n\n{base_system}"
