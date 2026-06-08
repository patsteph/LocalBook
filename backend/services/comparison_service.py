"""Comparison service — Phase 4 of v2-information-cortex.

Orchestrates side-by-side comparison of two sources:
  1. Fetch both source contents from `source_store`.
  2. Call `structured_llm.compare_documents()` → `DocumentComparison`.
  3. Wrap the result as a `json:comparison` Artifact envelope.

The frontend consumes the envelope and dispatches to
`ComparisonArtifactRenderer` for a side-by-side HTML card layout.

Failure semantics: if either source fetch fails, raise; if the comparison
itself fails, the structured_llm path already returns a partial result
with `synthesis="Comparison failed: ..."`, which the frontend renders as
the error state. Caller doesn't need to special-case.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import uuid4

from storage.source_store import source_store
from services.structured_llm import StructuredLLMService
from services.artifact_spec import json_artifact

logger = logging.getLogger(__name__)


class ComparisonService:
    """Compare two sources end-to-end, return an Artifact envelope."""

    def __init__(self) -> None:
        self._llm = StructuredLLMService()

    async def compare(
        self,
        notebook_id: str,
        source_a_id: str,
        source_b_id: str,
        focus: Optional[str] = None,
    ) -> dict:
        """Return a `json:comparison` Artifact envelope as a plain dict."""
        # Fetch both source contents (existing helper used by source viewer).
        content_a = await source_store.get_content(notebook_id, source_a_id)
        content_b = await source_store.get_content(notebook_id, source_b_id)
        if not content_a:
            raise ValueError(f"Source A not found: {source_a_id}")
        if not content_b:
            raise ValueError(f"Source B not found: {source_b_id}")

        title_a = content_a.get("filename") or "Source A"
        title_b = content_b.get("filename") or "Source B"
        text_a = (content_a.get("content") or "").strip()
        text_b = (content_b.get("content") or "").strip()

        # Optional focus prefix steers the LLM toward a specific axis of
        # comparison (e.g. "focus on the methodological differences").
        if focus:
            text_a = f"FOCUS: {focus}\n\n{text_a}"
            text_b = f"FOCUS: {focus}\n\n{text_b}"

        result = await self._llm.compare_documents(text_a, text_b)

        payload = {
            "source_a": {"id": source_a_id, "title": title_a},
            "source_b": {"id": source_b_id, "title": title_b},
            "similarities": result.similarities,
            "differences": result.differences,
            "unique_to_a": result.unique_to_first,
            "unique_to_b": result.unique_to_second,
            "synthesis": result.synthesis,
        }

        artifact = json_artifact(
            id=str(uuid4()),
            kind="comparison",
            payload=payload,
            title=f"{title_a} vs {title_b}",
            tagline=focus or None,
            metadata={
                "notebook_id": notebook_id,
                "source_a_id": source_a_id,
                "source_b_id": source_b_id,
            },
        )
        return artifact.model_dump()


comparison_service = ComparisonService()
