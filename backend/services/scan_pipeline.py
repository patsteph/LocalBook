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

from services.ollama_client import ollama_client
from services.progress_reporter import ProgressReporter, get_noop_reporter
from services.rag_engine import rag_engine
from storage.note_store import note_store
from api.constellation_ws import broadcast_update

logger = logging.getLogger(__name__)


# ── Prompts ─────────────────────────────────────────────────────────────────

_DOC_VISION_PROMPT = (
    "Extract all text, diagrams, and structure from this image. "
    "Output in markdown format."
)

_DOC_CLEANUP_SYSTEM = "You only output the cleaned text in markdown, no preamble."
_DOC_CLEANUP_PROMPT_TMPL = (
    "You are an expert editor. Clean up the following OCR text. "
    "Fix obvious typos, restore proper formatting, and remove weird artifacts. "
    "DO NOT change the meaning or add new information.\n\n"
    "OCR TEXT:\n{raw}"
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

VISION_MODEL = "granite3.1-vision:2b"
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
        if not file_paths:
            raise ValueError("process_batch: file_paths is empty")

        for p in file_paths:
            if not os.path.exists(p):
                raise FileNotFoundError(f"Image not found: {p}")

        rep = reporter or get_noop_reporter()
        total = len(file_paths)

        await rep.emit(
            "received", 2,
            f"Received {total} page{'s' if total != 1 else ''}",
            details={"total_pages": total, "mode": mode},
        )

        page_texts: List[str] = []
        # Reserve 5% for setup/received and 5% for final merge + save;
        # the middle 90% is divided among the N pages.
        per_page_span = max(1, int(90 / total))

        for idx, path in enumerate(file_paths, start=1):
            base_pct = 5 + (idx - 1) * per_page_span
            await rep.emit(
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
                await rep.emit(
                    f"page_{idx}_error",
                    base_pct + per_page_span,
                    f"Page {idx} failed — continuing with remaining pages",
                    details={"page": idx, "error": str(e)[:200]},
                )
            else:
                await rep.emit(
                    f"page_{idx}_done",
                    base_pct + per_page_span,
                    f"Page {idx} of {total} complete",
                    details={"page": idx, "total": total, "chars": len(text)},
                )
            page_texts.append(text)

        await rep.emit("merging", 95, "Merging pages…")
        merged = self._merge_pages(page_texts)

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

    # ── Core OCR / description of a single page ──────────────────────────────
    async def _ocr_one_page(self, file_path: str, *, mode: str) -> str:
        with open(file_path, "rb") as f:
            img_data = f.read()
        b64_image = base64.b64encode(img_data).decode("utf-8")

        if mode == "photo":
            logger.info(f"[scan] Granite Vision (photo) on {file_path}")
            raw = await ollama_client.vision_describe(
                image_b64=b64_image,
                prompt=_PHOTO_VISION_PROMPT,
                model=VISION_MODEL,
                api_style="generate",
            )
            logger.info("[scan] OLMo enrichment pass")
            result = await ollama_client.generate(
                prompt=_PHOTO_ENRICH_PROMPT_TMPL.format(raw=raw),
                model=PHOTO_ENRICH_MODEL,
                system=_PHOTO_ENRICH_SYSTEM,
                temperature=0.3,
            )
            return (result.get("response", raw) or raw).strip()

        # Default: document OCR
        logger.info(f"[scan] Granite Vision (document) on {file_path}")
        raw = await ollama_client.vision_describe(
            image_b64=b64_image,
            prompt=_DOC_VISION_PROMPT,
            model=VISION_MODEL,
            api_style="generate",
        )
        logger.info("[scan] Phi-4-mini cleanup pass")
        result = await ollama_client.generate(
            prompt=_DOC_CLEANUP_PROMPT_TMPL.format(raw=raw),
            model=DOC_CLEANUP_MODEL,
            system=_DOC_CLEANUP_SYSTEM,
            temperature=0.1,
        )
        return (result.get("response", raw) or raw).strip()

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
