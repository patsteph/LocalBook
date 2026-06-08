"""Artifact spec — the canonical envelope for anything LocalBook generates.

Mirror of the frontend TypeScript spec at `src/types/artifact.ts`. Backend
generation services wrap their outputs in an `Artifact` so the frontend
canvas can render via the single renderer registry.

Per `READFIRST/in-progress/v2-information-cortex.md` Phase 1.

Conventions:
- `type` values: 'markdown' | 'html' | 'svg' | 'mermaid' | 'klein' | 'json:<kind>'
  where <kind> identifies a structured payload schema (quiz, flashcards,
  chart, audio-player, video-player, note-editor, ...).
- `payload` is `Any` because the shape depends on `type`. Strict typing
  happens at the per-kind level in caller modules.
- `metadata` is the catch-all for fields that don't fit the spec cleanly
  (notebook_id, source_ids, criticScore, templateId, overlay flags, etc.).
  Migrate fields out of metadata into first-class spec fields when they
  become cross-cutting.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ArtifactAction(BaseModel):
    """An interactive affordance the renderer chrome may expose."""

    id: str
    label: str
    icon: Optional[str] = None


class Artifact(BaseModel):
    """Canonical artifact envelope. See module docstring for `type` values."""

    id: str
    type: str
    payload: Any

    # Display hints
    palette: Optional[str] = None
    title: Optional[str] = None
    tagline: Optional[str] = None

    # Interactive affordances surfaced by renderer chrome on the frontend.
    # Handlers are wired up frontend-side (we only carry intent here).
    actions: list[ArtifactAction] = Field(default_factory=list)

    # Free-form bag for migration of existing per-type metadata.
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─── Convenience constructors ─────────────────────────────────────────────
# Common types get small helpers so caller code stays terse. Add more as new
# generation services adopt the envelope.


def markdown_artifact(
    *,
    id: str,
    text: str,
    title: Optional[str] = None,
    tagline: Optional[str] = None,
    palette: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Artifact:
    return Artifact(
        id=id,
        type="markdown",
        payload=text,
        title=title,
        tagline=tagline,
        palette=palette,
        metadata=metadata or {},
    )


def html_artifact(
    *,
    id: str,
    html: str,
    title: Optional[str] = None,
    tagline: Optional[str] = None,
    palette: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Artifact:
    return Artifact(
        id=id,
        type="html",
        payload=html,
        title=title,
        tagline=tagline,
        palette=palette,
        metadata=metadata or {},
    )


def svg_artifact(
    *,
    id: str,
    svg: str,
    title: Optional[str] = None,
    tagline: Optional[str] = None,
    palette: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Artifact:
    return Artifact(
        id=id,
        type="svg",
        payload=svg,
        title=title,
        tagline=tagline,
        palette=palette,
        metadata=metadata or {},
    )


def mermaid_artifact(
    *,
    id: str,
    code: str,
    title: Optional[str] = None,
    tagline: Optional[str] = None,
    palette: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Artifact:
    return Artifact(
        id=id,
        type="mermaid",
        payload=code,
        title=title,
        tagline=tagline,
        palette=palette,
        metadata=metadata or {},
    )


def json_artifact(
    *,
    id: str,
    kind: str,
    payload: Any,
    title: Optional[str] = None,
    tagline: Optional[str] = None,
    palette: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Artifact:
    """Build a `json:<kind>` artifact for structured payloads (quiz, flashcards, etc.)."""
    return Artifact(
        id=id,
        type=f"json:{kind}",
        payload=payload,
        title=title,
        tagline=tagline,
        palette=palette,
        metadata=metadata or {},
    )
