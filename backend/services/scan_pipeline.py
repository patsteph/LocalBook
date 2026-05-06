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
from typing import Any, Dict, List, Optional

from config import settings
from services.memory_steward import free_for_pipeline
from services.ollama_client import ollama_client
from services.progress_reporter import ProgressReporter, get_noop_reporter
from services.rag_engine import rag_engine
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

    Vision succeeded and produced raw OCR but the polish-pass model
    (phi4-mini for documents, olmo2 for photos) errored. Less critical
    than a vision failure — in principle we could fall back to the raw
    OCR text, but for now we surface the error so the user knows the
    output is unpolished.
    """
    error_type = "cleanup_model"


# ── Prompts ───────────────────────────────────────────────────────────────────────────

# Auto-classification (fast, ~0.5s) — determines which OCR prompt to use.
_CLASSIFY_PROMPT = (
    "Classify this image into ONE category. Reply with only the word:\n"
    "document — printed or handwritten text, forms, letters, articles\n"
    "math — equations, formulas, mathematical notation\n"
    "whiteboard — whiteboard or blackboard with diagrams/text\n"
    "drawing — hand-drawn illustration, sketch, diagram\n"
    "photo — photograph of a scene, object, or person"
)

# Enhanced document OCR — handles math, color, structure
_DOC_VISION_PROMPT = (
    "Extract ALL content from this image with high fidelity:\n"
    "• Transcribe all text exactly, preserving structure and formatting\n"
    "• Mathematical equations → LaTeX notation ($inline$ or $$display$$)\n"
    "• Tables → markdown table format with alignment\n"
    "• Handwritten text → best transcription, [unclear] for illegible parts\n"
    "• Color annotations → note in [brackets] when meaningful "
    "(e.g. [red underline], [highlighted in yellow])\n"
    "• Diagrams → describe structure and labels\n"
    "Output clean markdown. No commentary."
)

_DOC_CLEANUP_SYSTEM = (
    "You output ONLY the cleaned text as plain markdown. "
    "Never wrap your output in code fences (no ```markdown, no ``` of any kind around the whole reply). "
    "Never add a LaTeX preamble (no \\documentclass, no \\usepackage, no \\usetikzlibrary). "
    "Never add commentary, greetings, or summaries (no 'Here is the cleaned text:'). "
    "Never invent a title, heading, or section the source page does not have. "
    "If the input is empty or unreadable, return an empty string."
)
_DOC_CLEANUP_PROMPT_TMPL = (
    "Clean up the following OCR text and return ONLY the cleaned markdown.\n"
    "Hard rules:\n"
    "• Output starts directly with the first line of content — no preamble, no fences.\n"
    "• Fix obvious OCR typos; do NOT rewrite, summarize, or add information.\n"
    "• PRESERVE LaTeX math ($...$ and $$...$$) verbatim.\n"
    "• PRESERVE color annotations in [brackets] verbatim.\n"
    "• PRESERVE table formatting and [unclear] markers verbatim.\n"
    "• If the source has no title, do not invent one.\n\n"
    "OCR TEXT:\n{raw}"
)

# Math-focused prompt
_MATH_VISION_PROMPT = (
    "This image contains mathematical content. Extract with precision:\n"
    "• All equations in LaTeX: inline $...$ and display $$...$$\n"
    "• Variable definitions and notation\n"
    "• Step-by-step derivations (preserve numbered steps)\n"
    "• Matrices and vectors in LaTeX: \\begin{pmatrix}...\\end{pmatrix}\n"
    "• Greek letters: \\alpha, \\beta, \\gamma, etc.\n"
    "• Surrounding explanatory text verbatim\n"
    "Output in markdown with LaTeX math blocks."
)

# Whiteboard/blackboard prompt
_WHITEBOARD_VISION_PROMPT = (
    "This is a whiteboard or blackboard. Extract:\n"
    "• All text, labels, and annotations verbatim\n"
    "• Diagrams → describe as structured lists with relationships\n"
    "• Arrows and connections: 'A → B', 'X connects to Y'\n"
    "• Boxes, circles, groupings: describe containment\n"
    "• Mathematical expressions in LaTeX\n"
    "• Color coding: note colors when they distinguish elements\n"
    "Output in organized markdown with sections."
)

# Drawing/sketch prompt
_DRAWING_VISION_PROMPT = (
    "This is a hand-drawn illustration or sketch. Describe:\n"
    "• Overall composition and layout\n"
    "• Individual elements with spatial relationships\n"
    "• Labels and annotations verbatim\n"
    "• Colors used and their significance\n"
    "• Artistic technique observations\n"
    "• Any text or writing present\n"
    "Output as structured markdown description."
)

_PHOTO_VISION_PROMPT = (
    "Describe this photo in rich detail. Include the overall composition, "
    "key objects, colors, lighting, atmosphere, and any text visible in the scene. "
    "Focus on creating a vivid textual representation of the image."
)

_PHOTO_ENRICH_SYSTEM = "You output beautifully formatted markdown descriptions."
_PHOTO_ENRICH_PROMPT_TMPL = (
    "You are an expert descriptive writer and image analyst. "
    "Enhance the following raw scene description into a highly structured, "
    "richly detailed markdown summary. Include a 'Reconstruction Prompt' section "
    "that could be used by an AI image generator to recreate this scene. "
    "Also extract 5-10 comma-separated keywords/tags.\n\n"
    "RAW SCENE:\n{raw}"
)

# Map content type → vision prompt
_MODE_PROMPTS = {
    "document": _DOC_VISION_PROMPT,
    "math": _MATH_VISION_PROMPT,
    "whiteboard": _WHITEBOARD_VISION_PROMPT,
    "drawing": _DRAWING_VISION_PROMPT,
    "photo": _PHOTO_VISION_PROMPT,
}

# Vision model is read dynamically from settings on each call so a runtime
# Locker swap (or LOCALBOOK_VISION_MODEL env override) takes effect without
# a backend restart. Was previously hardcoded to "granite3.1-vision:2b" which
# silently broke scans for any user that didn't have that exact tag pulled.
def _vision_model() -> str:
    return os.getenv("LOCALBOOK_VISION_MODEL") or settings.vision_model

DOC_CLEANUP_MODEL = "phi4-mini:latest"
PHOTO_ENRICH_MODEL = "olmo2:7b"

# Page separator used when merging multi-page scans into one note.
# Kept as a literal markdown horizontal rule so it renders cleanly in BlockNote
# and is trivial to split on for RAG chunking.
PAGE_SEPARATOR_TMPL = "\n\n---\n\n*Page {n}*\n\n"


class ScanPipeline:
    # ── Single-page entry point (watcher + legacy /scan/process) ─────────────
    async def process_image(
        self,
        file_path: str,
        notebook_id: Optional[str] = None,
        mode: str = "document",
    ) -> Dict[str, Any]:
        """Process one scanned image, create a note, and broadcast to the canvas."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Image not found: {file_path}")

        cleaned_text = await self._ocr_one_page(file_path, mode=mode)

        title, note_type, source_type = self._derive_note_meta(cleaned_text, mode)

        note = await note_store.create(
            notebook_id=notebook_id,
            title=title,
            content_markdown=cleaned_text,
            source_type=source_type,
            note_type=note_type,
            original_image_paths=[file_path],
        )

        await self._maybe_embed_photo(mode, notebook_id, note, cleaned_text, file_path)
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
                text = await self._ocr_one_page(path, mode=mode)
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
        )
        total = len(file_paths)

        # Use the first non-empty page to derive a title — usually the cover
        # or header page has the most useful signal.
        title_seed = next((t for t in page_texts if t.strip()), merged)
        title, note_type, source_type = self._derive_note_meta(title_seed, mode)
        # Tag multi-page scans explicitly so UI/agents can treat them specially.
        if total > 1:
            note_type = "scan_multi"

        await rep.emit("saving", 97, "Saving note…")
        note = await note_store.create(
            notebook_id=notebook_id,
            title=title,
            content_markdown=merged,
            source_type=source_type,
            note_type=note_type,
            original_image_paths=list(file_paths),
        )

        # Photo-mode semantic embedding applies per-page; for batch photo mode
        # we embed the merged description as a single document (simpler,
        # matches single-page semantics).
        await self._maybe_embed_photo(mode, notebook_id, note, merged, file_paths[0])
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
    ) -> Dict[str, Any]:
        """OCR a batch of pages and return the merged markdown WITHOUT creating
        a note. Used when the frontend wants to insert the OCR result directly
        into an open editor instead of spawning a separate scan note.

        Returns: {"merged_text": str, "page_texts": list[str], "total_pages": int, "chars": int}
        """
        rep = reporter or get_noop_reporter()
        page_texts, merged = await self._ocr_pages_and_merge(
            file_paths, mode=mode, reporter=rep, progress_end_pct=98,
        )
        total = len(file_paths)
        result = {
            "merged_text": merged,
            "page_texts": page_texts,
            "total_pages": total,
            "chars": len(merged),
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

    async def _ocr_one_page(self, file_path: str, *, mode: str) -> str:
        with open(file_path, "rb") as f:
            img_data = f.read()
        b64_image = base64.b64encode(img_data).decode("utf-8")
        vision_model_name = _vision_model()
        api_style = self._get_api_style(vision_model_name)

        if mode == "photo":
            logger.info(f"[scan] Granite Vision (photo) on {file_path}")
            raw = await ollama_client.vision_describe(
                image_b64=b64_image,
                prompt=_PHOTO_VISION_PROMPT,
                model=vision_model_name,
                api_style=api_style,
            )
            if raw.startswith("Error:"):
                raise VisionModelError(vision_model_name, raw[len('Error:'):].strip() or raw)

            logger.info("[scan] OLMo enrichment pass")
            result = await ollama_client.generate(
                prompt=_PHOTO_ENRICH_PROMPT_TMPL.format(raw=raw),
                model=PHOTO_ENRICH_MODEL,
                system=_PHOTO_ENRICH_SYSTEM,
                temperature=0.3,
            )
            response = result.get("response", raw) or raw
            if isinstance(response, str) and response.startswith("Error:"):
                raise CleanupModelError(PHOTO_ENRICH_MODEL, response[len('Error:'):].strip() or response)
            return response.strip()

        # Document / math / whiteboard / drawing — select prompt by mode
        prompt = _MODE_PROMPTS.get(mode, _DOC_VISION_PROMPT)
        logger.info(f"[scan] Vision ({mode}) on {file_path}")
        raw = await ollama_client.vision_describe(
            image_b64=b64_image,
            prompt=prompt,
            model=vision_model_name,
            api_style=api_style,
        )
        if raw.startswith("Error:"):
            raise VisionModelError(vision_model_name, raw[len('Error:'):].strip() or raw)

        logger.info("[scan] Phi-4-mini cleanup pass")
        result = await ollama_client.generate(
            prompt=_DOC_CLEANUP_PROMPT_TMPL.format(raw=raw),
            model=DOC_CLEANUP_MODEL,
            system=_DOC_CLEANUP_SYSTEM,
            temperature=0.1,
        )
        response = result.get("response", raw) or raw
        if isinstance(response, str) and response.startswith("Error:"):
            raise CleanupModelError(DOC_CLEANUP_MODEL, response[len('Error:'):].strip() or response)
        return response.strip()

    def _ocr_working_set(self, vision_model: str) -> set:
        """The set of Ollama models the OCR pipeline needs resident.

        Anything loaded outside this set is fair game for memory_steward to
        evict before the vision call runs. Computed dynamically so a future
        config where the *main* model also has vision support (e.g.
        gemma4:e4b) collapses to a single-model working set automatically.
        """
        keep = {vision_model, DOC_CLEANUP_MODEL, settings.embedding_model}
        # Photo enrichment uses a different downstream model; include it so
        # back-to-back photo captures don't churn the cache.
        keep.add(PHOTO_ENRICH_MODEL)
        # Drop empties (defensive — in case a setting is unset).
        return {m for m in keep if m}

    # ── Auto-classify + OCR (used by QR capture flow) ────────────────────────
    async def classify_and_ocr(self, file_path: str) -> tuple:
        """Auto-classify content type, then OCR with the right prompt.

        Returns (content_type: str, ocr_text: str).
        The vision model classifies each page (~0.5s), then the appropriate
        mode-specific prompt is used for extraction.
        """
        with open(file_path, "rb") as f:
            img_data = f.read()
        b64_image = base64.b64encode(img_data).decode("utf-8")
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

        # Step 1: Classify content type
        logger.info(f"[scan] Classifying {file_path}")
        classification = await ollama_client.vision_describe(
            image_b64=b64_image,
            prompt=_CLASSIFY_PROMPT,
            model=vision_model_name,
            api_style=api_style,
            num_predict=20,
        )
        if classification.startswith("Error:"):
            raise VisionModelError(
                vision_model_name,
                classification[len('Error:'):].strip() or classification,
            )

        content_type = classification.strip().lower().split()[0] if classification else "document"
        if content_type not in _MODE_PROMPTS:
            content_type = "document"
        logger.info(f"[scan] Classified as: {content_type}")

        # Step 2: OCR with mode-specific prompt
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
