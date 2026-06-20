"""
Scan Pipeline — image → OCR/scene → canvas note.

Supports two entry points:
- process_image(): single-page path used by the filesystem watcher (AirDrop /
  manual drops into ~/.../scans/) and legacy single-page API calls.
- process_batch(): multi-page path (Sprint 8) used by the scanning-session UI.
  Processes pages sequentially, emitting per-page progress via a
  ProgressReporter, and produces ONE merged note with page-break markers.

Both paths share the same Granite Vision + cleanup model stack.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any, Dict, List, Optional

from config import settings
from services.image_preprocessor import check_blur, enhance_for_ocr
from services.memory_steward import free_for_pipeline
from services.ollama_service import ollama_service
from services.page_classifier import classify_page
from services.progress_reporter import ProgressReporter, get_noop_reporter
from services.rag_engine import rag_engine
from services.vision_prompts import (
    CLASSIFY_PROMPT,
    CLEANUP_PROMPT_TMPL,
    CLEANUP_SYSTEM,
    DESCRIPTIVE_MODES,
    DOC_VISION_PROMPT,
    MODE_PROMPTS,
    PHOTO_ENRICH_PROMPT_TMPL,
    PHOTO_ENRICH_SYSTEM,
    PHOTO_KEYWORDS_PROMPT_TMPL,
    STRUCTURED_MODES,
)
from storage.note_store import note_store
from api.constellation_ws import broadcast_update

logger = logging.getLogger(__name__)


# ── Typed errors ─────────────────────────────────────────────────────────────────────────
# These are RuntimeError subclasses so existing `except Exception` paths
# keep working, but the capture queue inspects them with `isinstance` to
# label the failure precisely ("vision_model" vs "cleanup_model" vs
# generic) and the frontend then renders model-specific guidance.
# Carrying the model name lets users see *which* model failed regardless
# of which slot in their config they had it pointed at.
class PipelineModelError(RuntimeError):
    """Base for model-specific failures inside the scan pipeline."""
    error_type: str = "pipeline"

    def __init__(self, model: str, detail: str):
        self.model = model
        self.detail = detail
        super().__init__(f"{self.error_type} '{model}' failed: {detail}")


class VisionModelError(PipelineModelError):
    """The vision model failed to load, crashed, or returned an error.

    Most common causes (in observed frequency order):
      1. Model file is corrupt / incompatible with current Ollama runner
         (manifests as "model runner has unexpectedly stopped").
      2. Model is not pulled at all on this machine.
      3. OOM at load time — too many models resident.
      4. Model returned a vision-incapable response (rare; usually means
         the user pointed `vision_model` at a text-only model).
    """
    error_type = "vision_model"


class CleanupModelError(PipelineModelError):
    """The downstream cleanup / enrichment text model failed.

    The document path now falls back to the raw vision output so a
    transient cleanup failure doesn't discard a valid OCR pass — only
    the photo enrichment path still raises this, since photo output
    isn't useful without enrichment.
    """
    error_type = "cleanup_model"


class BlurryImageError(RuntimeError):
    """The captured image is too blurry for OCR.

    Surfaced before any LLM call so the user gets fast feedback to
    retake the photo. The capture queue maps this to error_type
    'blurry' for frontend-side guidance.
    """
    error_type = "blurry"

    def __init__(self, score: float):
        self.score = score
        super().__init__(f"Image too blurry to process (laplacian variance: {score:.1f})")


# ── Model resolution (dynamic — follows LLM Locker selections) ──────────

def _vision_model() -> str:
    """Vision model — env override > settings.vision_model.

    Read on every call so a runtime Locker swap takes effect without
    a backend restart.
    """
    return os.getenv("LOCALBOOK_VISION_MODEL") or settings.vision_model


def _cleanup_model() -> str:
    """Document OCR cleanup model — follows the active fast model.

    When the user swaps their fast model in the LLM Locker, cleanup
    follows automatically. Was previously hardcoded to phi4-mini.
    """
    return settings.ollama_fast_model


def _photo_enrich_model() -> str:
    """Photo enrichment model — follows the active main model.

    Photo enrichment benefits from the larger reasoning model since
    it produces structured prose, not just typo cleanup.
    """
    return settings.ollama_model

# Page separator used when merging multi-page scans into one note.
# Kept as a literal markdown horizontal rule so it renders cleanly in BlockNote
# and is trivial to split on for RAG chunking.
PAGE_SEPARATOR_TMPL = "\n\n---\n\n*Page {n}*\n\n"


# ── Refinement pass (A2) — main-model structure tidy for visual modes ──

# Modes that benefit from a structure-only refinement pass after vision
# OCR. The main reasoning model tidies the output without seeing the
# image — we constrain it strictly to "do not add information."
_REFINE_SYSTEMS = {
    "drawing": (
        "You tidy markdown descriptions of hand-drawn illustrations. "
        "Reformat sections for consistency. Do not add facts, observations, "
        "or details that are not present in the input. "
        "If a section is empty or unclear, leave it empty. "
        "Output only the cleaned markdown."
    ),
    "diagram": (
        "You tidy Mermaid diagrams. Validate node references and edge syntax. "
        "Normalize node labels by removing extraneous quotes only when safe. "
        "Do not add nodes, edges, or labels that are not in the input. "
        "Output only the cleaned markdown including the original ```mermaid fence."
    ),
    "whiteboard": (
        "You tidy markdown transcriptions of whiteboard photos. "
        "Cluster items into related groups using headings or sublists. "
        "Do not add bullet points, headings, or text that are not in the input. "
        "Output only the cleaned markdown."
    ),
}

_REFINE_PROMPTS = {
    "drawing": "Tidy this drawing description. Output only the cleaned markdown:\n\n{raw}",
    "diagram": "Validate and tidy this Mermaid diagram. Keep the same nodes and edges. Output only the cleaned markdown:\n\n{raw}",
    "whiteboard": "Tidy this whiteboard transcription. Keep the same topics and items. Output only the cleaned markdown:\n\n{raw}",
}


async def _refine_visual(raw: str, mode: str) -> str:
    """Run a structure-only refinement pass via the main reasoning model.

    Improves consistency of vision-model output for drawings, diagrams,
    and whiteboards without re-running vision. The refinement prompt is
    strict: "do not add information not present in the input." Returns
    the input unchanged on any failure (timeout, empty response, error).
    """
    if not raw or len(raw.strip()) < 50:
        return raw
    if mode not in _REFINE_SYSTEMS:
        return raw
    try:
        result = await ollama_service.generate(
            prompt=_REFINE_PROMPTS[mode].format(raw=raw),
            model=settings.ollama_model,
            system=_REFINE_SYSTEMS[mode],
            temperature=0.1,
            num_predict=2000,
        )
        refined = (result.get("response") or "").strip()
        if not refined or refined.startswith("Error:"):
            return raw
        return refined
    except Exception as e:
        logger.warning(f"[scan] Refinement pass failed for mode={mode}: {e}")
        return raw


# ── Translation pass (A3) — optional post-OCR translation ──

async def _translate_to(text: str, target_language: Optional[str]) -> str:
    """Optionally translate a markdown transcription into target_language.

    Returns text unchanged when target_language is None / empty / one of
    the original-language sentinels. On success, returns the original
    transcription followed by a '## Translation (lang)' section so the
    user gets both. Failure-safe — returns the original on any error.
    """
    if not text or not target_language:
        return text
    lang = target_language.strip()
    if lang.lower() in ("", "none", "original", "source", "auto"):
        return text
    system_prompt = (
        f"You translate markdown into {lang}. "
        "Preserve the markdown structure: headings stay as headings, lists as lists, "
        "tables as tables, code blocks as code blocks. "
        "Translate only natural-language text. Keep proper nouns, brand names, "
        "code identifiers, math, URLs, and [unclear] markers untranslated."
    )
    user_prompt = (
        f"Translate this markdown into {lang}. Output only the translation, "
        f"no commentary or wrapper:\n\n{text}"
    )
    try:
        result = await ollama_service.generate(
            prompt=user_prompt,
            model=settings.ollama_model,
            system=system_prompt,
            temperature=0.2,
            num_predict=4000,
        )
        translation = (result.get("response") or "").strip()
        if not translation or translation.startswith("Error:"):
            return text
        return f"{text}\n\n---\n\n## Translation ({lang})\n\n{translation}"
    except Exception as e:
        logger.warning(f"[scan] Translation pass failed (lang={lang}): {e}")
        return text


# ── Cross-page table stitching (C5) ──────────────────────────────────────

# A markdown table delimiter row: `|---|---|` etc.
_TABLE_DELIM_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
# Page separator emitted by _merge_pages — captures the marker so we can
# rebuild the output preserving page structure.
_PAGE_BREAK_RE = re.compile(r"(\n\n---\n\n\*Page \d+\*\n\n)")


def _parse_cells(row: str) -> List[str]:
    """Split a `| a | b | c |` row into trimmed cells `["a", "b", "c"]`."""
    m = _TABLE_ROW_RE.match(row)
    if not m:
        return []
    return [c.strip() for c in m.group(1).split("|")]


def _trailing_table_info(text: str) -> Optional[tuple[List[str], int]]:
    """If `text` ends with a markdown table, return (header_cells, last_row_index).

    Walks backwards from the end of the text, counting body rows, until it
    finds a delimiter row, then the header above it. Returns None if no
    well-formed table sits at the tail.
    """
    lines = text.splitlines()
    n = len(lines)
    # Skip trailing blank lines.
    last = n - 1
    while last >= 0 and not lines[last].strip():
        last -= 1
    if last < 2 or not _TABLE_ROW_RE.match(lines[last]) or _TABLE_DELIM_RE.match(lines[last]):
        return None
    # Walk back over body rows until we hit the delimiter row (delim ALSO
    # matches the row regex, so check for delim explicitly to stop).
    j = last
    while j >= 0 and _TABLE_ROW_RE.match(lines[j]) and not _TABLE_DELIM_RE.match(lines[j]):
        j -= 1
    if j < 1 or not _TABLE_DELIM_RE.match(lines[j]):
        return None
    header = _parse_cells(lines[j - 1])
    if not header:
        return None
    return (header, last)


def _leading_table_info(text: str) -> Optional[tuple[List[str], int, int]]:
    """If `text` starts (after blank lines) with a markdown table, return
    (header_cells, body_start_idx, body_end_idx_exclusive) — line indices
    into the splitlines() form of `text`."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i + 1 >= len(lines):
        return None
    # Header must be a row but not the delimiter (delim also matches the row regex).
    if not _TABLE_ROW_RE.match(lines[i]) or _TABLE_DELIM_RE.match(lines[i]):
        return None
    if not _TABLE_DELIM_RE.match(lines[i + 1]):
        return None
    header = _parse_cells(lines[i])
    body_start = i + 2
    body_end = body_start
    # Body rows: row regex matches and NOT a delimiter row.
    while body_end < len(lines) and _TABLE_ROW_RE.match(lines[body_end]) and not _TABLE_DELIM_RE.match(lines[body_end]):
        body_end += 1
    return (header, body_start, body_end)


def _stitch_cross_page_tables(merged: str) -> str:
    """Merge markdown tables that span page breaks.

    For each page-break separator, if the page above ends with a markdown
    table whose header matches the table at the top of the page below,
    drop the new header + delimiter rows and append the new body rows
    directly to the prior table. The page-break marker is replaced with
    a single newline so the table reads continuously.

    Conservative: only stitches when header cell-text matches exactly.
    Real distinct tables that happen to share dimensions but not headers
    pass through unchanged.
    """
    if not merged or "*Page" not in merged:
        return merged
    # Tokenize on the page-break marker so we can inspect each gap.
    tokens = _PAGE_BREAK_RE.split(merged)
    if len(tokens) < 3:
        return merged  # No page breaks present.

    # tokens = [page_a_text, sep_1, page_b_text, sep_2, page_c_text, ...]
    # We walk pairs and decide whether to stitch each gap.
    out: List[str] = [tokens[0]]
    i = 1
    while i + 1 < len(tokens):
        sep = tokens[i]
        next_text = tokens[i + 1]
        prev = out[-1]
        trailing = _trailing_table_info(prev)
        leading = _leading_table_info(next_text)
        if trailing and leading and trailing[0] == leading[0]:
            # Stitch: append new page's body rows onto prior table,
            # drop separator, keep the rest of the new page as a normal
            # post-table continuation.
            _, last_row = trailing
            new_header_cells, body_start, body_end = leading
            next_lines = next_text.splitlines()
            body_rows = next_lines[body_start:body_end]
            rest = "\n".join(next_lines[body_end:]).lstrip("\n")
            prev_lines = prev.splitlines()
            stitched_prev = "\n".join(prev_lines[: last_row + 1] + body_rows + prev_lines[last_row + 1 :])
            out[-1] = stitched_prev
            if rest:
                out.append("\n\n" + rest)
        else:
            out.append(sep)
            out.append(next_text)
        i += 2
    return "".join(out)


# ── Confidence scoring (C1) ──────────────────────────────────────────────

def _compute_confidence(text: str) -> float:
    """Compute a 0-1 confidence score from [unclear] marker density.

    1.0 = no unclear markers; 0.0 = every word is unclear. Returns 1.0 on
    empty / no-text input so a structured (table, vCard, code) page that
    legitimately has zero prose words isn't flagged as low confidence.
    """
    if not text or not text.strip():
        return 1.0
    word_count = len(text.split())
    if word_count == 0:
        return 1.0
    unclear_count = text.lower().count("[unclear]")
    if unclear_count == 0:
        return 1.0
    return max(0.0, 1.0 - (unclear_count / word_count))


# ── Prompt-echo / empty-output sanity gate ───────────────────────────────
#
# Small vision models (Granite 2B, Gemma3 4B, phi3-vision) sometimes fail
# to OCR a page and echo fragments of the instruction prompt instead —
# phrases like "Output only the transcription" or "Use H2 (##)". The
# downstream cleanup pass then polishes the echo into fluent-looking
# instructions that get saved as the page's content, presenting to the
# user as a "lost" page in the middle of a multi-page scan. This helper
# detects that pattern so we can retry once with a bare prompt before
# falling back to a re-scan marker.

_PROMPT_ECHO_TELLS = (
    "output only the page text",
    "output only the transcription",
    "output only the description",
    "output only the contact block",
    "output only the metadata block",
    "output only the structured transcription",
    "output only the fenced code blocks",
    "output only the cleaned markdown",
    "output a clear markdown description",
    "output only the mermaid block",
    "if a word is too blurry",
    "use h2 (##)",
    "use h3 (###)",
    "render markers in the body",
    "render each entry",
    "begin with the recipe name",
    "begin with an h2",
)


def _looks_like_prompt_echo(raw: str) -> bool:
    """Return True when the vision output looks like an echo of the prompt.

    Triggers (any one suffices):
      - Output shorter than 30 non-whitespace chars (too thin to be useful).
      - Output contains 2+ distinct prompt-template phrases.

    Conservative on purpose — real OCR rarely contains the literal sentence
    "Output only the transcription", so a single hit isn't enough to flag.
    """
    if not raw:
        return True
    text = raw.strip()
    if len(text) < 30:
        return True
    lower = text.lower()
    hits = sum(1 for tell in _PROMPT_ECHO_TELLS if tell in lower)
    return hits >= 2


# ── Mermaid diagram validation (diagram mode) ────────────────────────────

# Match a fenced ```mermaid ... ``` block. Used to validate diagram-mode
# vision output before it reaches BlockNote.
_MERMAID_BLOCK_RE = re.compile(
    r"```mermaid\s*\n(.+?)\n```",
    re.DOTALL | re.IGNORECASE,
)
# Recognized Mermaid diagram-type keywords. If none appears inside the block,
# the model produced a "mermaid" fence but not actual Mermaid syntax — we
# treat that as malformed and fall back.
_MERMAID_TYPE_KEYWORDS = (
    "graph ",
    "graph\n",
    "flowchart ",
    "flowchart\n",
    "mindmap",
    "sequencediagram",
    "classdiagram",
    "statediagram",
    "erdiagram",
    "gantt",
    "pie ",
    "journey",
    "timeline",
)


def _validate_mermaid(raw: str) -> str:
    """Return the original markdown if it contains a valid Mermaid block.

    "Valid" here means: a ```mermaid fence exists AND its body starts with
    one of the recognized diagram-type keywords. If not, we strip the fence
    and emit the body as a plain code block so the user still sees the
    transcription, even if BlockNote can't render it as a diagram. Either
    way the user gets readable output rather than a broken diagram.
    """
    if not raw:
        return raw
    m = _MERMAID_BLOCK_RE.search(raw)
    if not m:
        return raw  # No fence at all — leave as-is (the prompt may have summarized only).
    body = m.group(1).strip().lower()
    first_token = body[:80]
    if any(kw in first_token for kw in _MERMAID_TYPE_KEYWORDS):
        return raw  # Looks valid.
    # Malformed: relabel the fence so BlockNote renders it as plain text
    # instead of attempting a Mermaid render that would error out.
    logger.warning("[scan] Diagram-mode produced an unrecognized Mermaid body — falling back to plain text.")
    return _MERMAID_BLOCK_RE.sub(
        lambda mt: f"```text\n{mt.group(1)}\n```",
        raw,
        count=1,
    )


# ── Heading normalization (cross-page consistency) ───────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
# Line that is only hashes + whitespace — an empty heading the LLM left behind.
_EMPTY_HEADING_RE = re.compile(r"^\s*#{1,6}\s*$")
# Toggle for fenced code blocks — the normalizer must skip lines inside
# fences so a Python comment ('# foo') or shell prompt isn't mistaken for a
# heading. Document-class modes rarely have fences, but better safe than
# stripping a real code-block content line.
_FENCE_TOGGLE_RE = re.compile(r"^\s*```")


def _extract_heading_trail(md: str, depth: int = 3) -> List[str]:
    """Return the last `depth` markdown headings from `md`, in order.

    Used to feed cross-page context to the next page's vision prompt so a
    section continuing across pages keeps the same heading level.
    """
    if not md:
        return []
    matches = _HEADING_RE.findall(md)
    if not matches:
        return []
    return [f"{hashes} {text}".strip() for hashes, text in matches[-depth:]]


def _normalize_headings(md: str, prior_trail: Optional[List[str]] = None) -> str:
    """Enforce a consistent H2/H3 hierarchy on a single page's markdown.

    Three transforms:
      1. Demote any H1 (#) to H2 (##). The source filename already carries
         the document title; H1 in body content drives the H1-vs-H3 drift
         that motivated this normalizer.
      2. Strip empty headings (lines that are just ## or ### with no text).
      3. If a heading on this page exactly repeats a heading from
         prior_trail, demote it one level (a section continuing across
         pages should not start a new section header at the same level).
    """
    if not md or not md.strip():
        return md

    # Build a normalized set of prior-trail texts for duplicate detection.
    prior_texts: set[str] = set()
    if prior_trail:
        for h in prior_trail:
            m = _HEADING_RE.match(h)
            if m:
                prior_texts.add(m.group(2).strip().lower())

    out_lines: list[str] = []
    in_fence = False
    for line in md.splitlines():
        # Track fence state and pass fence lines through verbatim.
        if _FENCE_TOGGLE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue
        # Drop lines that are nothing but hashes + whitespace.
        if _EMPTY_HEADING_RE.match(line):
            continue
        m = _HEADING_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        hashes, heading_text = m.group(1), m.group(2).strip()
        # Strip empties (heading text was just whitespace).
        if not heading_text:
            continue
        level = len(hashes)
        # Demote H1 → H2 unconditionally.
        if level == 1:
            level = 2
        # Demote duplicate-of-prior-trail headings by one level (cap at H6).
        if heading_text.lower() in prior_texts and level < 6:
            level += 1
        out_lines.append(f"{'#' * level} {heading_text}")
    return "\n".join(out_lines)


class ScanPipeline:
    # ── Single-page entry point (watcher + legacy /scan/process) ─────────────
    async def process_image(
        self,
        file_path: str,
        notebook_id: Optional[str] = None,
        mode: str = "document",
        *,
        target_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process one scanned image, create a note, and broadcast to the canvas.

        Args:
            target_language: Optional. When set, OCR output is followed by a
                translation section in this language. Default None (no translation).
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Image not found: {file_path}")

        cleaned_text = await self._ocr_one_page(
            file_path, mode=mode, target_language=target_language,
        )
        confidence = _compute_confidence(cleaned_text)

        title, note_type, source_type = self._derive_note_meta(cleaned_text, mode)

        note = await note_store.create(
            notebook_id=notebook_id,
            title=title,
            content_markdown=cleaned_text,
            source_type=source_type,
            note_type=note_type,
            original_image_paths=[file_path],
        )
        # Stash confidence + mode on the note so the UI can show a badge
        # and downstream logic can route low-confidence pages for re-capture.
        note["confidence"] = confidence
        note["mode"] = mode

        await self._maybe_embed_photo(mode, notebook_id, note, cleaned_text, file_path)
        await self._maybe_link_business_card(mode, notebook_id, note, cleaned_text)
        await self._broadcast_created(note, [file_path])
        return note

    # ── Shared OCR+merge core (used by both note-creating and inline paths) ──
    async def _ocr_pages_and_merge(
        self,
        file_paths: List[str],
        *,
        mode: str,
        reporter: ProgressReporter,
        progress_end_pct: int = 95,
        target_language: Optional[str] = None,
    ) -> tuple[List[str], str]:
        """Run OCR over each page, emit progress, and return (page_texts, merged_text).

        progress_end_pct controls how much of the 0-100 progress range this
        OCR phase consumes. The note-creating path leaves room (95%) for
        save + broadcast; the inline path can use 100% for OCR alone.
        """
        if not file_paths:
            raise ValueError("file_paths is empty")
        for p in file_paths:
            if not os.path.exists(p):
                raise FileNotFoundError(f"Image not found: {p}")

        total = len(file_paths)
        await reporter.emit(
            "received", 2,
            f"Received {total} page{'s' if total != 1 else ''}",
            details={"total_pages": total, "mode": mode},
        )

        # Evict non-pipeline models once before the batch so the vision
        # call has guaranteed RAM. Mirrors what classify_and_ocr does for
        # the single-page path. On 16-18 GB Macs the main reasoning model
        # (~6 GB) competing with vision (~4.6 GB) is the most common cause
        # of a silent mid-batch failure. Best-effort — non-fatal on error.
        try:
            evicted = await free_for_pipeline(
                self._ocr_working_set(_vision_model()),
                reason="scan_batch",
            )
            if evicted:
                logger.info(f"[scan-batch] freed RAM by unloading: {evicted}")
        except Exception as _e:
            logger.warning(f"[scan-batch] free_for_pipeline failed: {_e}")

        page_texts: List[str] = []
        # Reserve 5% for setup/received and (100 - progress_end_pct)% for the
        # caller's post-OCR work; the middle range is divided among N pages.
        ocr_span = max(1, progress_end_pct - 5)
        per_page_span = max(1, ocr_span // total)

        for idx, path in enumerate(file_paths, start=1):
            base_pct = 5 + (idx - 1) * per_page_span
            await reporter.emit(
                f"page_{idx}_start",
                base_pct,
                f"Processing page {idx} of {total}…",
                details={"page": idx, "total": total, "filename": os.path.basename(path)},
            )
            try:
                # Cross-page context: pass the tail of the prior successful
                # page so the model handles paragraphs spanning page breaks,
                # and the last few headings so heading hierarchy stays
                # consistent across the batch.
                prev_tail = None
                prior_trail: List[str] = []
                for prev in reversed(page_texts):
                    if prev and not prev.startswith("*[Page"):
                        prev_tail = prev[-200:]
                        prior_trail = _extract_heading_trail(prev, depth=3)
                        break
                text = await self._ocr_one_page(
                    path,
                    mode=mode,
                    prev_page_tail=prev_tail,
                    prior_headings=prior_trail,
                    target_language=target_language,
                )
            except Exception as e:
                logger.error(f"[scan-batch] Page {idx} failed: {e}")
                text = f"*[Page {idx} failed to process: {e}]*"
                await reporter.emit(
                    f"page_{idx}_error",
                    base_pct + per_page_span,
                    f"Page {idx} failed — continuing with remaining pages",
                    details={"page": idx, "error": str(e)[:200]},
                )
            else:
                await reporter.emit(
                    f"page_{idx}_done",
                    base_pct + per_page_span,
                    f"Page {idx} of {total} complete",
                    details={"page": idx, "total": total, "chars": len(text)},
                )
            page_texts.append(text)

        await reporter.emit("merging", progress_end_pct, "Merging pages…")
        merged = self._merge_pages(page_texts)
        return page_texts, merged

    # ── Multi-page entry point (Sprint 8 — scanning sessions) ────────────────
    async def process_batch(
        self,
        file_paths: List[str],
        *,
        notebook_id: Optional[str] = None,
        mode: str = "document",
        reporter: Optional[ProgressReporter] = None,
        target_language: Optional[str] = None,
        append_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process an ordered batch of pages and produce ONE merged note.

        Pages are processed sequentially so we can emit granular progress
        ("Processing page 3 of 8…") and so any per-page failure is isolated.
        On partial failure we still produce a note containing the successful
        pages plus an error marker for the failed ones — the user can re-run
        or manually edit rather than losing the whole session.
        """
        rep = reporter or get_noop_reporter()
        page_texts, merged = await self._ocr_pages_and_merge(
            file_paths, mode=mode, reporter=rep, progress_end_pct=95,
            target_language=target_language,
        )
        # Stitch markdown tables that span page boundaries (C5) — only
        # applies to document-class modes where tables are common.
        if mode not in DESCRIPTIVE_MODES and mode not in STRUCTURED_MODES:
            merged = _stitch_cross_page_tables(merged)
        total = len(file_paths)
        confidence = _compute_confidence(merged)

        await rep.emit("saving", 97, "Saving note…")

        # Append-to-existing-note path (C3): if append_to is supplied,
        # update the existing note instead of creating a new one. The
        # merged content is appended after a horizontal rule separator.
        # Falls through to create-new on any failure (note doesn't exist,
        # update returns None) so the user never loses the capture.
        note = None
        if append_to:
            existing = await note_store.get(append_to)
            if existing is None:
                logger.warning(f"[scan] append_to={append_to} not found; creating new note instead")
            else:
                existing_md = existing.get("content_markdown") or ""
                separator = "\n\n---\n\n" if existing_md.strip() else ""
                combined = f"{existing_md}{separator}{merged}"
                existing_paths = existing.get("original_image_paths") or []
                if isinstance(existing_paths, str):
                    try:
                        import json as _json
                        existing_paths = _json.loads(existing_paths)
                    except Exception:
                        existing_paths = []
                merged_paths = list(existing_paths) + list(file_paths)
                note = await note_store.update(
                    append_to,
                    content_markdown=combined,
                    original_image_paths=merged_paths,
                )
                if note is None:
                    logger.warning(f"[scan] append_to={append_to} update returned None; creating new note instead")

        if note is None:
            # Use the first non-empty page to derive a title — usually the
            # cover or header page has the most useful signal.
            title_seed = next((t for t in page_texts if t.strip()), merged)
            title, note_type, source_type = self._derive_note_meta(title_seed, mode)
            # Tag multi-page scans explicitly so UI/agents can treat them specially.
            if total > 1:
                note_type = "scan_multi"
            note = await note_store.create(
                notebook_id=notebook_id,
                title=title,
                content_markdown=merged,
                source_type=source_type,
                note_type=note_type,
                original_image_paths=list(file_paths),
            )

        # Stamp confidence + mode for UI badging.
        note["confidence"] = confidence
        note["mode"] = mode

        # Photo-mode semantic embedding applies per-page; for batch photo mode
        # we embed the merged description as a single document (simpler,
        # matches single-page semantics).
        await self._maybe_embed_photo(mode, notebook_id, note, merged, file_paths[0])
        await self._maybe_link_business_card(mode, notebook_id, note, merged)
        await self._broadcast_created(note, list(file_paths))

        await rep.complete(
            f"Ready — {total} page{'s' if total != 1 else ''} merged into 1 note",
            details={
                "note_id": note["id"],
                "total_pages": total,
                "chars": len(merged),
                "title": title,
            },
        )
        return note

    # ── Inline OCR (returns text without creating a note) ────────────────────
    async def process_batch_inline(
        self,
        file_paths: List[str],
        *,
        mode: str = "document",
        reporter: Optional[ProgressReporter] = None,
        target_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """OCR a batch of pages and return the merged markdown WITHOUT creating
        a note. Used when the frontend wants to insert the OCR result directly
        into an open editor instead of spawning a separate scan note.

        Returns: {"merged_text": str, "page_texts": list[str], "total_pages": int, "chars": int}
        """
        rep = reporter or get_noop_reporter()
        page_texts, merged = await self._ocr_pages_and_merge(
            file_paths, mode=mode, reporter=rep, progress_end_pct=98,
            target_language=target_language,
        )
        if mode not in DESCRIPTIVE_MODES and mode not in STRUCTURED_MODES:
            merged = _stitch_cross_page_tables(merged)
        total = len(file_paths)
        result = {
            "merged_text": merged,
            "page_texts": page_texts,
            "total_pages": total,
            "chars": len(merged),
            "confidence": _compute_confidence(merged),
        }
        await rep.complete(
            f"Ready — {total} page{'s' if total != 1 else ''} ready to insert",
            details=result,
        )
        return result

    # ── Core OCR / description of a single page ──────────────────────────────
    def _get_api_style(self, model_name: str) -> str:
        """Determine the API style for the current vision model from the registry."""
        try:
            from evaluator.model_registry import model_registry
            info = model_registry.get_model(model_name)
            if info:
                return info.vision_api_style
        except Exception as _e:
            logger.debug(f"[scan] api_style lookup failed: {_e}")
        return "generate"

    async def _ocr_one_page(
        self,
        file_path: str,
        *,
        mode: str,
        prev_page_tail: Optional[str] = None,
        prior_headings: Optional[List[str]] = None,
        target_language: Optional[str] = None,
        refine: bool = True,
    ) -> str:
        """Run OCR on a single page.

        Args:
            file_path: path to the image
            mode: content type (one of the labels in vision_prompts.MODE_PROMPTS)
            prev_page_tail: optional last ~200 chars of the prior page,
                injected as continuation context. Helps the vision model
                handle paragraphs that span page boundaries.
            prior_headings: last few markdown headings from the prior page,
                so a section continuing across pages keeps the same level.
                Only meaningful for document-class modes.
            target_language: Optional language name (e.g. 'Spanish', 'French').
                When set, the OCR output is followed by a translation section.
                None (default) skips translation.
            refine: When True (default), drawing/diagram/whiteboard modes get
                a structure-only refinement pass via the main reasoning
                model after vision OCR. Set False to skip (testing, speed).
        """
        with open(file_path, "rb") as f:
            img_data = f.read()

        # Blur gate — fast feedback for unreadable captures, before any
        # LLM call. Saves 2-5 seconds and gives the user clear guidance.
        too_blurry, blur_score = check_blur(img_data)
        if too_blurry:
            logger.info(f"[scan] Blur gate rejected {file_path} (score={blur_score:.1f})")
            raise BlurryImageError(blur_score)

        # Mode-aware preprocessing: deskew, perspective-correct, CLAHE
        # for document modes; pass-through for photo/drawing.
        # Failure-safe — returns original bytes on any internal error.
        img_data = enhance_for_ocr(img_data, mode)
        b64_image = base64.b64encode(img_data).decode("utf-8")

        vision_model_name = _vision_model()
        api_style = self._get_api_style(vision_model_name)

        if mode == "photo":
            return await self._ocr_photo(b64_image, vision_model_name, api_style)

        # Build the vision prompt from the per-mode template. Cross-page
        # context (one prior heading + a compact tail of the prior page)
        # is appended AFTER the main instruction — small vision models
        # follow the most recent instruction more reliably than the first,
        # and a heavy prepended prefix is the most common trigger for the
        # "middle page echoes the prompt" failure mode. Framing is positive
        # ("keep using…") instead of negative ("do not start…").
        bare_prompt = MODE_PROMPTS.get(mode, DOC_VISION_PROMPT)
        prompt = bare_prompt
        if mode not in DESCRIPTIVE_MODES and mode not in STRUCTURED_MODES:
            ctx_parts: List[str] = []
            if prior_headings:
                last_h = prior_headings[-1].strip()
                if last_h:
                    ctx_parts.append(f"The previous page's most recent heading was: {last_h}.")
            if prev_page_tail:
                compact = prev_page_tail[-80:].replace("\n", " ").strip()
                if compact:
                    ctx_parts.append(f'It ended with: "{compact}".')
            if ctx_parts:
                prompt = (
                    f"{bare_prompt}\n\n"
                    f"Context: {' '.join(ctx_parts)} "
                    "If this page continues that section, keep using the same heading level."
                )
        logger.info(f"[scan] Vision ({mode}) on {file_path}")
        raw = await ollama_service.vision_describe(
            image_b64=b64_image,
            prompt=prompt,
            model=vision_model_name,
            api_style=api_style,
            temperature=0.1,
        )
        if raw.startswith("Error:"):
            raise VisionModelError(vision_model_name, raw[len('Error:'):].strip() or raw)

        # Sanity gate: vision output that looks like prompt-echo or that
        # is too short to be useful gets one retry with the bare per-mode
        # prompt (no cross-page context) at temperature 0.0 — the prefix
        # is the most common echo trigger. If the retry still fails, write
        # a clear re-scan marker instead of letting the cleanup pass turn
        # echoed instructions into fluent-looking content.
        if _looks_like_prompt_echo(raw):
            logger.warning(
                f"[scan] Vision sanity gate fired for {file_path} "
                f"(mode={mode}, model={vision_model_name}, len={len(raw.strip())}); "
                "retrying with bare prompt at temp 0.0"
            )
            retry = await ollama_service.vision_describe(
                image_b64=b64_image,
                prompt=bare_prompt,
                model=vision_model_name,
                api_style=api_style,
                temperature=0.0,
            )
            if retry.startswith("Error:") or _looks_like_prompt_echo(retry):
                logger.warning(f"[scan] Vision retry also failed sanity for {file_path}; emitting re-scan marker")
                return "*[Page OCR unreliable — please re-scan this page.]*"
            raw = retry

        # Structured modes (diagram / receipt / business_card / code) emit
        # a single primary block whose structure cleanup would corrupt.
        # Skip the cleanup pass and head straight to heading normalization.
        if mode in STRUCTURED_MODES:
            cleaned_raw = raw.strip()
            # Diagram mode: validate Mermaid AND optionally refine structure.
            if mode == "diagram":
                cleaned_raw = _validate_mermaid(cleaned_raw)
                if refine:
                    cleaned_raw = await _refine_visual(cleaned_raw, "diagram")
                    cleaned_raw = _validate_mermaid(cleaned_raw)
            normalized = _normalize_headings(cleaned_raw, prior_headings)
            return await _translate_to(normalized, target_language)

        # Descriptive modes — drawing runs through optional refinement;
        # photo path returned earlier. No cleanup model pass for either.
        if mode in DESCRIPTIVE_MODES:
            cleaned = raw.strip()
            if refine and mode == "drawing":
                cleaned = await _refine_visual(cleaned, "drawing")
            return await _translate_to(cleaned, target_language)

        # Document-class modes: cleanup pass + heading normalization.
        # Cleanup follows the active fast model. Falls back to raw OCR if
        # cleanup fails so a transient cleanup error doesn't discard a
        # valid vision pass.
        cleanup_model = _cleanup_model()
        logger.info(f"[scan] Cleanup pass with model={cleanup_model}")
        try:
            result = await ollama_service.generate(
                prompt=CLEANUP_PROMPT_TMPL.format(raw=raw),
                model=cleanup_model,
                system=CLEANUP_SYSTEM,
                temperature=0.1,
            )
            response = result.get("response", "") or ""
            if not response or (isinstance(response, str) and response.startswith("Error:")):
                logger.warning(f"[scan] Cleanup empty/error from {cleanup_model}; using raw OCR")
                cleaned = raw.strip() + "\n\n*[cleanup skipped]*"
            else:
                cleaned = response.strip()
        except Exception as e:
            logger.warning(f"[scan] Cleanup exception from {cleanup_model}: {e}; using raw OCR")
            cleaned = raw.strip() + "\n\n*[cleanup skipped]*"

        normalized = _normalize_headings(cleaned, prior_headings)
        # Whiteboard gets the optional refinement pass too — its output is
        # a structured transcription that benefits from topic clustering.
        if refine and mode == "whiteboard":
            normalized = await _refine_visual(normalized, "whiteboard")
        return await _translate_to(normalized, target_language)

    async def _ocr_photo(
        self,
        b64_image: str,
        vision_model_name: str,
        api_style: str,
    ) -> str:
        """Photo path: scene → enrichment markdown → keywords → merge.

        Split into two narrow enrichment calls (markdown + keywords) so
        each has a single, simple format requirement that small models
        can follow reliably. Previously a single call asked for three
        outputs and small models scrambled the format.
        """
        logger.info("[scan] Vision (photo)")
        raw = await ollama_service.vision_describe(
            image_b64=b64_image,
            prompt=MODE_PROMPTS["photo"],
            model=vision_model_name,
            api_style=api_style,
        )
        if raw.startswith("Error:"):
            raise VisionModelError(vision_model_name, raw[len('Error:'):].strip() or raw)

        enrich_model = _photo_enrich_model()
        logger.info(f"[scan] Photo enrichment with model={enrich_model}")

        # Call 1: structured markdown summary + reconstruction prompt
        try:
            result = await ollama_service.generate(
                prompt=PHOTO_ENRICH_PROMPT_TMPL.format(raw=raw),
                model=enrich_model,
                system=PHOTO_ENRICH_SYSTEM,
                temperature=0.3,
            )
            enriched = (result.get("response") or "").strip()
            if not enriched or enriched.startswith("Error:"):
                # Fall back to raw description rather than failing the page
                logger.warning(f"[scan] Photo enrich empty/error; using raw scene")
                enriched = raw.strip()
        except Exception as e:
            logger.warning(f"[scan] Photo enrich exception: {e}; using raw scene")
            enriched = raw.strip()

        # Call 2: keywords (separate call so the format is enforceable)
        try:
            kw_result = await ollama_service.generate(
                prompt=PHOTO_KEYWORDS_PROMPT_TMPL.format(raw=raw),
                model=enrich_model,
                system=PHOTO_ENRICH_SYSTEM,
                temperature=0.3,
                num_predict=60,
            )
            kw_text = (kw_result.get("response") or "").strip()
            # Sanity: keep the line only if it looks like a comma-separated list
            # and not a stray paragraph or LLM apology.
            if kw_text and "," in kw_text and len(kw_text) < 400 and not kw_text.startswith("Error:"):
                # Take first line only — defends against trailing commentary
                first_line = kw_text.splitlines()[0].strip()
                enriched = enriched.rstrip() + f"\n\n**Tags:** {first_line}"
        except Exception as e:
            logger.debug(f"[scan] Photo keywords skipped: {e}")

        return enriched

    def _ocr_working_set(self, vision_model: str) -> set:
        """The set of Ollama models the OCR pipeline needs resident.

        Anything loaded outside this set is fair game for memory_steward to
        evict before the vision call runs. Computed dynamically so a future
        config where the *main* model also has vision support (e.g.
        gemma4:e4b) collapses to a single-model working set automatically.
        """
        keep = {
            vision_model,
            _cleanup_model(),       # follows settings.ollama_fast_model
            _photo_enrich_model(),  # follows settings.ollama_model
            settings.embedding_model,
        }
        # Drop empties (defensive — in case a setting is unset).
        return {m for m in keep if m}

    # ── Auto-classify + OCR (used by QR capture flow) ────────────────────────
    async def classify_and_ocr(self, file_path: str) -> tuple:
        """Auto-classify content type, then OCR with the right prompt.

        Returns (content_type: str, ocr_text: str).

        Classification runs heuristic-first (cheap image stats, ~50ms) and
        only invokes the LLM for ambiguous pages. For ~80% of typical scans
        this saves the 1-2s the LLM classification used to cost.
        """
        with open(file_path, "rb") as f:
            img_data = f.read()
        vision_model_name = _vision_model()
        api_style = self._get_api_style(vision_model_name)

        # Free RAM for the vision model. On a 16-18 GB box the chat main
        # model (~6 GB) will OOM-crash Ollama's runner when vision (~4.6 GB)
        # tries to load alongside it. Evicting models that aren't part of
        # the OCR pipeline working set guarantees the vision call has room.
        # Best-effort: any failure here is logged and the call proceeds.
        try:
            evicted = await free_for_pipeline(
                self._ocr_working_set(vision_model_name),
                reason="classify_and_ocr",
            )
            if evicted:
                logger.info(f"[scan] freed RAM by unloading: {evicted}")
        except Exception as _e:
            logger.warning(f"[scan] memory_steward free_for_pipeline failed: {_e}")

        # Step 1: Heuristic-first classification with LLM fallback
        async def _llm_classify(bytes_in: bytes) -> str:
            b64 = base64.b64encode(bytes_in).decode("utf-8")
            classification = await ollama_service.vision_describe(
                image_b64=b64,
                prompt=CLASSIFY_PROMPT,
                model=vision_model_name,
                api_style=api_style,
                num_predict=20,
            )
            if classification.startswith("Error:"):
                raise VisionModelError(
                    vision_model_name,
                    classification[len('Error:'):].strip() or classification,
                )
            return classification

        content_type = await classify_page(img_data, _llm_classify)
        logger.info(f"[scan] Classified {file_path} as: {content_type}")

        # Step 2: OCR with mode-specific prompt (preprocessing happens inside)
        ocr_text = await self._ocr_one_page(file_path, mode=content_type)
        return (content_type, ocr_text)


    # ── Helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _merge_pages(page_texts: List[str]) -> str:
        """Join pages with a page-break marker. Single-page input is returned as-is."""
        if len(page_texts) == 1:
            return page_texts[0]
        parts: List[str] = []
        for idx, text in enumerate(page_texts, start=1):
            if idx == 1:
                parts.append(text.rstrip())
            else:
                parts.append(PAGE_SEPARATOR_TMPL.format(n=idx))
                parts.append(text.rstrip())
        return "\n".join(parts).strip() + "\n"

    @staticmethod
    def _derive_note_meta(sample_text: str, mode: str) -> tuple[str, str, str]:
        """Return (title, note_type, source_type) for a scan."""
        if mode == "photo":
            return "Photo Scene Description", "photo", "scanned_photo"

        title = "Scanned Note"
        first_line = sample_text.split("\n", 1)[0].strip()
        if first_line and len(first_line) < 50:
            candidate = first_line.replace("#", "").strip()
            if candidate:
                title = candidate
        return title, "scan", "scanned"

    async def _maybe_link_business_card(
        self,
        mode: str,
        notebook_id: Optional[str],
        note: Dict[str, Any],
        content_md: str,
    ) -> None:
        """Best-effort: parse a business-card capture and seed a People Profiler entry.

        Runs only for `mode == "business_card"` with a notebook_id present. Any
        failure is non-fatal — the user still gets the markdown note even if
        the people-profile create fails or the parse can't find a name.
        """
        if mode != "business_card" or not notebook_id or not content_md:
            return
        try:
            # Extract name from the H2 emitted by BUSINESS_CARD_VISION_PROMPT.
            name_match = re.search(r"^##\s+(.+?)\s*$", content_md, flags=re.MULTILINE)
            if not name_match:
                logger.debug("[scan] business_card: no H2 name found, skipping link")
                return
            name = name_match.group(1).strip()
            # Guard against the model echoing the prompt template literally.
            if not name or name.upper() in ("NAME", "VENDOR NAME"):
                return

            # Pull bold-prefixed fields like '**Title:** Engineer'.
            fields: Dict[str, str] = {}
            for m in re.finditer(r"\*\*([^*]+):\*\*\s*(.+?)\s*$", content_md, flags=re.MULTILINE):
                fields[m.group(1).strip().lower()] = m.group(2).strip()

            # Collect free-text fragments for the initial notes.
            phone = fields.get("phone") or fields.get("mobile") or ""
            address = fields.get("address") or ""
            initial_notes_parts: List[str] = ["Captured from business card."]
            if phone:
                initial_notes_parts.append(f"Phone: {phone}.")
            if address:
                initial_notes_parts.append(f"Address: {address}.")

            social_links: Dict[str, str] = {}
            for key in ("website", "twitter", "linkedin", "github"):
                if fields.get(key):
                    social_links[key] = fields[key]

            from api.people import _load_config, _save_config
            from models.person_profile import PersonProfile, CoachingNote

            config = _load_config(notebook_id)
            # Avoid duplicates by name (case-insensitive). Real users may have
            # two contacts with the same name — we still call this best-effort
            # and let the user manually disambiguate via the People panel.
            existing_names = {(m.name or "").strip().lower() for m in config.members}
            if name.lower() in existing_names:
                logger.info(f"[scan] business_card: '{name}' already exists in People, skipping create")
                return

            person = PersonProfile(
                notebook_id=notebook_id,
                name=name,
                social_links=social_links,
                email=fields.get("email", ""),
                current_role=fields.get("title", ""),
                current_company=fields.get("organization") or fields.get("company") or "",
                tags=["from_business_card_capture"],
                collection_schedule=config.collection_schedule,
            )
            person.coaching_notes.append(
                CoachingNote(text=" ".join(initial_notes_parts), category="general")
            )
            config.members.append(person)
            _save_config(notebook_id, config)
            note["linked_person_id"] = person.id
            logger.info(f"[scan] business_card: linked '{name}' as new People profile {person.id}")
        except Exception as e:
            logger.warning(f"[scan] business_card auto-link failed (non-fatal): {e}")

    async def _maybe_embed_photo(
        self,
        mode: str,
        notebook_id: Optional[str],
        note: Dict[str, Any],
        text: str,
        representative_path: str,
    ) -> None:
        """Embed photo-mode scene description into LanceDB for semantic search."""
        if mode != "photo" or not notebook_id:
            return
        try:
            filename = os.path.basename(representative_path)
            logger.info(f"[scan] Embedding photo scene into LanceDB: {filename}")
            await rag_engine.ingest_document(
                notebook_id=notebook_id,
                source_id=note["id"],
                text=text,
                filename=filename,
                source_type="photo",
            )
        except Exception as e:
            logger.error(f"[scan] Photo embedding failed (non-fatal): {e}")

    @staticmethod
    async def _broadcast_created(note: Dict[str, Any], image_paths: List[str]) -> None:
        """Notify any connected frontends that a new scan-note exists."""
        try:
            await broadcast_update("canvas_item_created", {
                "id": note["id"],
                "type": "note",
                "title": note["title"],
                "content": note["content_markdown"],
                "metadata": {
                    "sourceType": "scanned",
                    "originalImagePaths": image_paths,
                    "persistedNoteId": note["id"],
                },
            })
        except Exception as e:
            logger.debug(f"[scan] WS broadcast failed (non-fatal): {e}")


scan_pipeline = ScanPipeline()
