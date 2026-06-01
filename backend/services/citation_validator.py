"""Citation contract — validation utilities for `[Sn]` source tags.

The citation contract (Tier 1.1, 2026-05-31) attaches a stable `[Sn]` tag
to each source chunk inside the context_builder output. Downstream prompts
ask the LLM to suffix every factual claim with the `[Sn]` of the chunk
that grounds it. This module is the post-processing safety net — it
strips bogus tags (n outside the valid set) and, optionally, removes
sentences/lines that lack any valid citation.

Two strictness modes:
  - `strip_invalid_citations`: minimum mode — only removes `[Sn]` tokens
    whose n is not in the valid set. Sentences without citations remain.
  - `enforce_citations`: strict mode — also removes any sentence that has
    no valid `[Sn]` after stripping. Use for source-grounded documents
    where uncited claims should not survive (briefings, FAQs, study
    guides). Avoid for prose-heavy outputs where some narrative
    connective tissue is fine (explainers, podcasts).

The validator is intentionally regex-based, not LLM-based — the goal is
deterministic verifiability. A second-pass LLM judge could be added
later if needed.
"""
from __future__ import annotations

import re
from typing import Iterable, Set

# Matches `[S12]`, `[s1]`, `[ S3 ]`. Captures the digits.
_CITATION_RE = re.compile(r"\[\s*[Ss](\d+)\s*\]")

# Matches a sentence's trailing punctuation so we can keep it after we strip
# a citation that sits between the last word and the period. e.g.
# "Claim here [S99]." → "Claim here ." → we collapse the space.
_PRE_PUNCT_SPACE_RE = re.compile(r" +([.,;:!?])")


def find_citations(text: str) -> Set[int]:
    """Return the set of distinct n values referenced in `text` via `[Sn]`."""
    return {int(m.group(1)) for m in _CITATION_RE.finditer(text)}


def strip_invalid_citations(text: str, valid_indices: Iterable[int]) -> str:
    """Remove `[Sn]` tokens whose n is not in `valid_indices`.

    Preserves all other content. The single most common downstream call:
    after generation, before display.
    """
    valid = {int(n) for n in valid_indices}

    def _sub(m: re.Match) -> str:
        n = int(m.group(1))
        return m.group(0) if n in valid else ""

    cleaned = _CITATION_RE.sub(_sub, text)
    # Collapse the space we leave behind when a citation sat between the
    # last word and trailing punctuation: "...here ." → "...here."
    cleaned = _PRE_PUNCT_SPACE_RE.sub(r"\1", cleaned)
    return cleaned


# Sentence boundary: keeps the terminator. Splits on `. `, `! `, `? `, newline.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def enforce_citations(
    text: str,
    valid_indices: Iterable[int],
    *,
    drop_uncited_lines: bool = True,
) -> str:
    """Strict mode: strip invalid citations AND drop sentences with no valid one.

    Splits on sentence boundaries (`. `, `! `, `? `, newlines) and discards
    any sentence that contains zero valid `[Sn]` tags after stripping
    invalid ones. Preserves the original line/paragraph structure where
    possible.

    Skips markdown headings (lines starting with `#`), list bullets
    (`-`, `*`, `1.`), and blank lines so structure survives.
    """
    valid = {int(n) for n in valid_indices}
    # First strip invalid tokens so we judge based on what survives.
    cleaned = strip_invalid_citations(text, valid)

    if not drop_uncited_lines:
        return cleaned

    # Process line-by-line so we keep markdown structure intact.
    out_lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        # Pass-through: blank, headings, list markers, code, table separators.
        if (
            not stripped
            or stripped.startswith(("#", "-", "*", ">", "|", "```"))
            or re.match(r"^\d+\.\s", stripped)
        ):
            out_lines.append(line)
            continue
        # Sentence-level filter within the line.
        sentences = _SENT_SPLIT_RE.split(line)
        kept = [s for s in sentences if find_citations(s) & valid]
        if kept:
            out_lines.append(" ".join(kept))
        # else: drop the whole line (no cited sentence survives)
    return "\n".join(out_lines)


def citation_legend(sources_map: dict) -> str:
    """Format a `[Sn] filename` legend for prompt injection. Convenience —
    `BuiltContext.citation_legend()` is the preferred call site."""
    if not sources_map:
        return ""
    return "\n".join(
        f"[S{n}] {fname}" for n, fname in sorted(sources_map.items())
    )
