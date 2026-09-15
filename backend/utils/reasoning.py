"""Reasoning-trace removal — ONE implementation, for production and the Evaluator.

Why this exists (2026-09-14). The Evaluator has stripped reasoning blocks before scoring since
the Locker rebuild (`RunProfile.normalize_filters` defaults to `strip_thinking`, always on).
Production never did. The only thing standing between a reasoning model and the user was a
hand-written `stop_sequences` entry in `known_models.json` — gemma has `<|channel>thought`,
and a model with no row, or one that reasons in `<think>` instead, has nothing.

That asymmetry points the wrong way: **the harness was more forgiving than the app.** A
reasoning model could score well in evaluation — because the scorer never saw the reasoning —
and then leak `<think>…</think>` into chat answers, generated documents and podcast scripts.
An evaluator that passes a model the app cannot use is worse than no evaluator, and it is the
exact failure mode the Evaluator overhaul exists to remove.

So: strip in the production seam, for every model, and let the Evaluator delegate here. A new
model then needs no registry row to be safe — which is the point of "flexible as new models
drop".

Streaming needs its own treatment: a block cannot be removed until its closing tag arrives, and
the tag itself can be split across chunks. `ReasoningStreamFilter` withholds text from the
moment an opener *might* be starting and releases it once the question is settled, so a partial
`<thi` is never emitted and then regretted.

Two limits, stated rather than hidden:

- **False positives are possible.** Output that legitimately contains `<think>` — a user asking
  about the tag itself, or generated code — will have that span removed. Accepted deliberately:
  leaking a model's private deliberation into every answer is both likelier and worse than
  mangling a rare discussion of the literal tag. Revisit if it ever bites in practice.
- **gemma's `<|channel>thought` markers are NOT handled here.** They are unpaired and are
  suppressed at the sampler via `stop_sequences` in the registry instead. This module covers
  the `<think>`-family convention, which is what an unregistered model is most likely to use.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Opener/closer pairs, longest-first so a prefix match cannot shadow a longer tag.
THINK_TAGS: List[Tuple[str, str]] = [
    ("<think>", "</think>"),
    ("<reasoning>", "</reasoning>"),
    ("<thought>", "</thought>"),
    ("◁think▷", "◁/think▷"),   # Kimi
]

_MAX_OPEN_TAG = max(len(o) for o, _ in THINK_TAGS)


def strip_reasoning(text: str) -> str:
    """Remove reasoning traces, returning the model's FINAL answer.

    - Closed blocks are removed wherever they appear.
    - An UNCLOSED opener (reasoning that hit the token cap, or a model that emitted nothing
      but reasoning) means no final answer was produced → everything from the opener onward is
      dropped, so a caller sees "no answer" rather than the reasoning masquerading as one.
    - No markers → returned unchanged.
    """
    if not text or not isinstance(text, str):
        return text or ""
    out = text
    for open_tag, close_tag in THINK_TAGS:
        out = re.sub(
            re.escape(open_tag) + r"[\s\S]*?" + re.escape(close_tag),
            "",
            out,
            flags=re.IGNORECASE,
        )
        m = re.search(re.escape(open_tag), out, flags=re.IGNORECASE)
        if m:
            out = out[: m.start()]
    return out.strip()


def looks_like_reasoning(text: str) -> bool:
    """Did this output contain a reasoning trace at all?

    A FIT observation, not a quality one. The Evaluator reports it so "this model reasons
    out loud" shows up as a fact about the model rather than being silently normalized away.
    """
    if not text:
        return False
    low = text.lower()
    return any(o.lower() in low for o, _ in THINK_TAGS)


class ReasoningStreamFilter:
    """Removes reasoning blocks from a token stream, chunk by chunk.

    Usage:
        f = ReasoningStreamFilter()
        for chunk in stream:
            out = f.feed(chunk)
            if out:
                yield out
        tail = f.flush()          # emits a held partial that turned out not to be a tag

    Two hazards this exists to handle:

    1. **Split tags.** `<think>` can arrive as `<thi` + `nk>`. Emitting `<thi` and deciding
       afterwards is not an option — it is already on the user's screen. So any text that
       could still become an opener is HELD until enough characters arrive to settle it.
    2. **Unclosed reasoning.** If the stream ends inside a block, everything after the opener
       is discarded, matching `strip_reasoning`. The user sees an empty answer, which is the
       truth, instead of the model's private deliberation.
    """

    def __init__(self) -> None:
        self._held = ""           # text that might be the start of an opener
        self._in_block = False
        self._closer: Optional[str] = None

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        buf = self._held + chunk
        self._held = ""
        out = []

        while buf:
            if self._in_block:
                assert self._closer is not None
                idx = buf.lower().find(self._closer.lower())
                if idx == -1:
                    # Still inside the block. Keep only enough tail to catch a split closer.
                    keep = len(self._closer) - 1
                    self._held = buf[-keep:] if keep > 0 else ""
                    return "".join(out)
                buf = buf[idx + len(self._closer):]
                self._in_block = False
                self._closer = None
                continue

            # Not in a block: find the earliest opener.
            best_idx, best_pair = -1, None
            low = buf.lower()
            for open_tag, close_tag in THINK_TAGS:
                i = low.find(open_tag.lower())
                if i != -1 and (best_idx == -1 or i < best_idx):
                    best_idx, best_pair = i, (open_tag, close_tag)

            if best_idx == -1:
                # No complete opener. A TRAILING partial might still become one, so hold back
                # the last few characters rather than emitting text we may have to retract.
                hold = _trailing_partial_len(buf)
                if hold:
                    self._held = buf[-hold:]
                    buf = buf[:-hold]
                out.append(buf)
                return "".join(out)

            out.append(buf[:best_idx])
            self._in_block = True
            self._closer = best_pair[1]
            buf = buf[best_idx + len(best_pair[0]):]

        return "".join(out)

    def flush(self) -> str:
        """Final call. Emits a held partial that never became a tag.

        If the stream ended INSIDE a reasoning block, nothing is emitted — an unclosed block
        means there was no final answer.
        """
        if self._in_block:
            self._held = ""
            return ""
        tail, self._held = self._held, ""
        return tail

    @property
    def stripped_anything(self) -> bool:
        """True once a reasoning block has been seen — the streaming fit signal."""
        return self._in_block or self._closer is not None


def _trailing_partial_len(buf: str) -> int:
    """How many trailing characters could still grow into an opener.

    `"...and <thi"` must hold 4 characters; `"...done."` holds none. Bounded by the longest
    opener, so the hold can never grow without limit.
    """
    max_check = min(len(buf), _MAX_OPEN_TAG - 1)
    for n in range(max_check, 0, -1):
        tail = buf[-n:].lower()
        for open_tag, _ in THINK_TAGS:
            if open_tag.lower().startswith(tail):
                return n
    return 0
