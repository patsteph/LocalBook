"""Output-normalization FILTERS for the evaluator (Locker rebuild — build B).

The core reason non-olmo/gemma models scored terribly: raw model output was fed
straight into the scorer, so a thinking model's <think> blocks or a differently-
templated model's wrapping looked like garbage. lm-evaluation-harness's fix is to
put normalization in FILTERS that run on the raw output BEFORE scoring — so the
harness measures the model, not its fit to our defaults.

These filters are engine-agnostic and pure (no I/O). A task declares a `filter_list`
(build C wires it into scoring); the two universally-useful filters are here:
  • strip_thinking  — remove reasoning traces (<think>…</think> and kin)
  • extract_json    — pull the JSON payload out of chatty output

Design notes grounded in the 2026-07-07 research:
  - Qwen3 with thinking ON ALWAYS emits <think>…</think> (possibly empty), so strip
    DEFENSIVELY — never assume the block is absent.
  - Reasoning conventions vary (<think>, <reasoning>, DeepSeek/Qwen <think>); newer
    runners (Ollama) return reasoning in a SEPARATE field, but we still strip
    in-band as a belt-and-suspenders.
"""
from __future__ import annotations

import json
import re
from typing import Optional, Protocol, runtime_checkable

# Reasoning-block markers seen across families. Matched case-insensitively.
_THINK_TAGS = [
    ("<think>", "</think>"),
    ("<reasoning>", "</reasoning>"),
    ("<thought>", "</thought>"),
    ("◁think▷", "◁/think▷"),   # Kimi
]


def strip_thinking(text: str) -> str:
    """Remove reasoning traces, returning the model's FINAL answer.

    - Closed blocks (<think>…</think>) are removed wherever they appear.
    - An UNCLOSED opener (reasoning that hit the token cap with no closer, or a
      model that emitted only reasoning) means no final answer was produced →
      everything from the opener onward is dropped (scorer fairly sees "no answer"
      rather than scoring the reasoning as if it were the answer).
    - No markers → returned unchanged.
    """
    if not text or not isinstance(text, str):
        return text or ""
    out = text
    for open_tag, close_tag in _THINK_TAGS:
        # Remove all closed blocks (non-greedy, dot-all, case-insensitive).
        out = re.sub(
            re.escape(open_tag) + r"[\s\S]*?" + re.escape(close_tag),
            "",
            out,
            flags=re.IGNORECASE,
        )
        # Drop a dangling unclosed opener + everything after it.
        m = re.search(re.escape(open_tag), out, flags=re.IGNORECASE)
        if m:
            out = out[: m.start()]
    return out.strip()


def extract_json(text: str) -> Optional[str]:
    """Return the JSON substring from possibly-chatty output, or None.

    Order: a ```json fenced block → any ``` fenced block that parses → the first
    balanced top-level {...} or [...] that parses. Returns the JSON *string*
    (scorers parse it); does not repair — pair with utils.json_repair if needed.
    """
    if not text or not isinstance(text, str):
        return None
    t = strip_thinking(text).strip()

    def _try(s: str) -> Optional[str]:
        s = s.strip()
        try:
            json.loads(s)
            return s
        except Exception:
            return None

    # Whole thing is JSON?
    whole = _try(t)
    if whole is not None:
        return whole
    # Fenced ```json … ``` (then any ``` … ```).
    for pat in (r"```json\s*\n?([\s\S]*?)```", r"```\s*\n?([\s\S]*?)```"):
        for m in re.finditer(pat, t, flags=re.IGNORECASE):
            got = _try(m.group(1))
            if got is not None:
                return got
    # First balanced {...} / [...] that parses.
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = t.find(open_c)
        while start != -1:
            depth = 0
            for i in range(start, len(t)):
                if t[i] == open_c:
                    depth += 1
                elif t[i] == close_c:
                    depth -= 1
                    if depth == 0:
                        got = _try(t[start : i + 1])
                        if got is not None:
                            return got
                        break
            start = t.find(open_c, start + 1)
    return None


def trim_preamble(text: str) -> str:
    """Drop a leading conversational preamble ('Sure, here is…:', 'Answer:') when
    it's clearly a wrapper around the real content. Conservative — only strips a
    short lead-in that ends with a colon on the first line."""
    if not text:
        return text or ""
    t = strip_thinking(text)
    m = re.match(r"^\s*(?:sure|certainly|here(?:'s| is)|okay|ok|answer|response)\b[^\n:]{0,60}:\s*", t, flags=re.IGNORECASE)
    return t[m.end():].lstrip() if m else t


# ── Filter protocol + registry (lm-eval-harness "filter_list" shape) ─────────────
@runtime_checkable
class Filter(Protocol):
    def __call__(self, text: str) -> str: ...


# Named, composable filters a task can list by key. `json` returns "" when no JSON
# is found so the JSON-validity scorer fails cleanly rather than scoring prose.
FILTERS: dict[str, Filter] = {
    "strip_thinking": strip_thinking,
    "trim_preamble": trim_preamble,
    "json": lambda t: extract_json(t) or "",
}


def apply_filters(text: str, filter_names: list[str]) -> str:
    """Run a named filter pipeline over raw output. Unknown names are skipped."""
    out = text or ""
    for name in filter_names or []:
        f = FILTERS.get(name)
        if f is not None:
            out = f(out)
    return out
