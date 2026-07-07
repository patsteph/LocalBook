"""Robust JSON parsing for LLM output.

Local models (llama3.2, phi4-mini) frequently produce malformed JSON:
  - Trailing commas in objects/arrays
  - JSON wrapped in markdown fences (```json ... ```)
  - Preamble text before/after the actual JSON
  - Single quotes instead of double quotes (rare)

This module centralizes the cleanup logic that was previously duplicated
across theme_extractor.py, visual_analyzer.py, contradiction_detector.py,
knowledge_graph.py, rag_engine.py, and others.
"""
import json
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MARKDOWN_FENCE_RE = re.compile(r"```(?:json)?\s*\n?([\s\S]*?)```", re.IGNORECASE)
_TRAILING_COMMA_OBJ_RE = re.compile(r",\s*}")
_TRAILING_COMMA_ARR_RE = re.compile(r",\s*]")

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_JSON_FENCE_RE = re.compile(r"```json\b[\s\S]*?```", re.IGNORECASE)


def sanitize_prose_output(text: str) -> str:
    """Defend a PROSE-intended LLM output against JSON / think-block leakage.

    Curator (and other) surfaces prompt for prose but occasionally get a JSON
    object, a ```json fenced block, or a `<think>…</think>` residue from the
    model — which then renders raw to the user (the 2026-07-07 "raw JSON in
    Curator text" report). This strips those; if what REMAINS is a bare
    top-level JSON object/array (the model ignored the prose instruction
    entirely), it returns "" so the caller falls back to its own prose default.
    Never raises. Prose that merely contains braces is preserved — the discard
    only fires when the whole thing actually parses as JSON.
    """
    if not text or not isinstance(text, str):
        return ""
    t = _THINK_RE.sub("", text)
    t = _JSON_FENCE_RE.sub("", t).strip()
    if t[:1] in ("{", "[") and t[-1:] in ("}", "]"):
        try:
            json.loads(t)
            return ""  # entire output is a JSON blob → not prose
        except Exception:
            pass  # looks JSON-ish but isn't valid JSON → keep (likely real prose)
    return t


def robust_json_parse(
    raw: str,
    *,
    expect: str = "object",
    fallback: Any = None,
    label: str = "",
) -> Any:
    """Parse JSON from raw LLM output with automatic cleanup.

    Args:
        raw:      Raw text from LLM (may contain markdown fences, preamble, etc.)
        expect:   "object" to extract {...}, "array" to extract [...], or "any"
        fallback: Value to return if all parsing attempts fail (default None)
        label:    Optional context label for log messages (e.g. "ThemeExtractor")

    Returns:
        Parsed JSON (dict, list, etc.) or *fallback* on failure.
    """
    if not raw or not raw.strip():
        return fallback

    text = raw.strip()

    # Attempt 1: direct parse (already valid JSON)
    try:
        return json.loads(text)
    except json.JSONDecodeError as _e:
        logger.debug(f"[json-repair] {type(_e).__name__}: {_e}")

    # Attempt 2: strip markdown fences
    fence_match = _MARKDOWN_FENCE_RE.search(text)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            text = fence_match.group(1).strip()

    # Attempt 3: extract the outermost JSON structure
    extracted = _extract_json(text, expect)
    if extracted is not None:
        # Fix trailing commas (most common LLM error)
        cleaned = _TRAILING_COMMA_OBJ_RE.sub("}", extracted)
        cleaned = _TRAILING_COMMA_ARR_RE.sub("]", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as _e:
            logger.debug(f"[json-repair] {type(_e).__name__}: {_e}")

    tag = f"[{label}] " if label else ""
    logger.debug(f"{tag}robust_json_parse failed on: {raw[:200]}...")
    return fallback


def _extract_json(text: str, expect: str) -> Optional[str]:
    """Find the outermost JSON object or array in text."""
    if expect == "array":
        return _extract_balanced(text, "[", "]")
    elif expect == "object":
        return _extract_balanced(text, "{", "}")
    else:
        # Try object first, then array
        result = _extract_balanced(text, "{", "}")
        if result is None:
            result = _extract_balanced(text, "[", "]")
        return result


def _extract_balanced(text: str, open_char: str, close_char: str) -> Optional[str]:
    """Extract a balanced delimited region from text.

    Handles nested structures and quoted strings (won't match braces
    inside string literals).
    """
    start = text.find(open_char)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    # Unbalanced — return from start to last occurrence of close_char
    end = text.rfind(close_char)
    if end > start:
        return text[start : end + 1]

    return None
