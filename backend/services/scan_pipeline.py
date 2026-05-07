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
from services.image_preprocessor import check_blur, enhance_for_ocr
from services.memory_steward import free_for_pipeline
from services.ollama_client import ollama_client
from services.page_classifier import classify_page
from services.progress_reporter import ProgressReporter, get_noop_reporter
from services.rag_engine import rag_engine
from services.vision_prompts import (
    CLASSIFY_PROMPT,
    CLEANUP_PROMPT_TMPL,
    CLEANUP_SYSTEM,
    DOC_VISION_PROMPT,
    MODE_PROMPTS,
    PHOTO_ENRICH_PROMPT_TMPL,
    PHOTO_ENRICH_SYSTEM,
    PHOTO_KEYWORDS_PROMPT_TMPL,
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
                # Cross-page context: pass the tail of the prior successful
                # page so the model handles paragraphs spanning page breaks.
                prev_tail = None
                for prev in reversed(page_texts):
                    if prev and not prev.startswith("*[Page"):
                        prev_tail = prev[-200:]
                        break
                text = await self._ocr_one_page(
                    path, mode=mode, prev_page_tail=prev_tail
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

    async def _ocr_one_page(
        self,
        file_path: str,
        *,
        mode: str,
        prev_page_tail: Optional[str] = None,
    ) -> str:
        """Run OCR on a single page.

        Args:
            file_path: path to the image
            mode: content type (document/math/whiteboard/drawing/photo)
            prev_page_tail: optional last ~200 chars of the prior page,
                injected as continuation context. Helps the vision model
                handle paragraphs that span page boundaries.
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

        # Document / math / whiteboard / drawing — select prompt by mode
        prompt = MODE_PROMPTS.get(mode, DOC_VISION_PROMPT)
        if prev_page_tail:
            prompt = (
                "This page continues from a previous page. The previous page ended with:\n"
                f"\"{prev_page_tail}\"\n\n"
                f"{prompt}"
            )
        logger.info(f"[scan] Vision ({mode}) on {file_path}")
        raw = await ollama_client.vision_describe(
            image_b64=b64_image,
            prompt=prompt,
            model=vision_model_name,
            api_style=api_style,
        )
        if raw.startswith("Error:"):
            raise VisionModelError(vision_model_name, raw[len('Error:'):].strip() or raw)

        # Cleanup pass — follows the active fast model. Falls back to
        # raw OCR if cleanup fails so a transient cleanup error doesn't
        # discard a valid vision pass.
        cleanup_model = _cleanup_model()
        logger.info(f"[scan] Cleanup pass with model={cleanup_model}")
        try:
            result = await ollama_client.generate(
                prompt=CLEANUP_PROMPT_TMPL.format(raw=raw),
                model=cleanup_model,
                system=CLEANUP_SYSTEM,
                temperature=0.1,
            )
            response = result.get("response", "") or ""
            if not response or (isinstance(response, str) and response.startswith("Error:")):
                logger.warning(f"[scan] Cleanup empty/error from {cleanup_model}; using raw OCR")
                return raw.strip() + "\n\n*[cleanup skipped]*"
            return response.strip()
        except Exception as e:
            logger.warning(f"[scan] Cleanup exception from {cleanup_model}: {e}; using raw OCR")
            return raw.strip() + "\n\n*[cleanup skipped]*"

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
        raw = await ollama_client.vision_describe(
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
            result = await ollama_client.generate(
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
            kw_result = await ollama_client.generate(
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
            classification = await ollama_client.vision_describe(
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
