"""Linked Folders API — link a directory to a notebook and keep it ingested.

Mounted at `/folders`. Everything here is a thin shell over
`storage/folder_link_store.py` (state) and `services/folder_watcher.py`
(scanning); no scanning or ingest logic lives in this file.
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storage.folder_link_store import (
    DEFAULT_PATTERNS,
    VALID_FREQUENCIES,
    FolderLinkPathError,
    folder_link_store,
    validate_folder_path,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateLinkRequest(BaseModel):
    path: str
    notebook_id: Optional[str] = None      # None => Smart Folder
    patterns: Optional[List[str]] = None
    frequency: str = "hourly"
    recursive: bool = False
    enabled: bool = True
    # "all"      — ingest what is already in the folder (the default)
    # "new_only" — treat everything currently present as already accounted for
    #              and only ingest files that arrive from now on.
    backfill: str = "all"


class UpdateLinkRequest(BaseModel):
    patterns: Optional[List[str]] = None
    frequency: Optional[str] = None
    recursive: Optional[bool] = None
    enabled: Optional[bool] = None
    notebook_id: Optional[str] = None


async def _decorate(link: Dict[str, Any], titles: Dict[str, str]) -> Dict[str, Any]:
    """Attach the counts and the notebook name the UI needs to render a row."""
    out = dict(link)
    out["notebook_title"] = titles.get(link.get("notebook_id") or "", None)
    out["stats"] = folder_link_store.stats(link["id"])
    p = Path(link["path"])
    out["exists"] = p.is_dir()
    out["display_path"] = str(p).replace(str(Path.home()), "~", 1)
    return out


async def _notebook_titles() -> Dict[str, str]:
    try:
        from storage.notebook_store import notebook_store
        nbs = await notebook_store.list()
        return {n["id"]: n.get("title") or n.get("name") or "Untitled" for n in nbs}
    except Exception:
        return {}


@router.get("/links")
async def list_links(notebook_id: Optional[str] = None):
    """Every linked folder, or just one notebook's."""
    titles = await _notebook_titles()
    links = folder_link_store.list_links(notebook_id)
    rows = [await _decorate(l, titles) for l in links]
    totals = {
        "folders": len(rows),
        "ingested": sum(r["stats"]["ingested"] for r in rows),
        "failed": sum(r["stats"]["failed"] for r in rows),
        "smart": sum(1 for r in rows if r["is_smart"]),
    }
    return {"links": rows, "totals": totals,
            "frequencies": list(VALID_FREQUENCIES),
            "default_patterns": DEFAULT_PATTERNS}


@router.post("/links")
async def create_link(req: CreateLinkRequest):
    try:
        link = folder_link_store.create_link(
            path=req.path, notebook_id=req.notebook_id,
            patterns=req.patterns, frequency=req.frequency,
            recursive=req.recursive, enabled=req.enabled,
        )
    except FolderLinkPathError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if req.backfill == "new_only":
        # A ledger write, not a scan — the existing files are never opened.
        from services.folder_watcher import folder_watcher
        try:
            baselined = await asyncio.to_thread(folder_watcher.mark_baseline, link)
            logger.info(f"[folder-links] baselined {baselined} existing files for {link['path']}")
        except (OSError, PermissionError) as e:
            folder_link_store.delete_link(link["id"])
            raise HTTPException(status_code=400,
                                detail=folder_watcher._access_error(link["path"], e))

    titles = await _notebook_titles()
    return await _decorate(link, titles)


@router.patch("/links/{link_id}")
async def update_link(link_id: str, req: UpdateLinkRequest):
    if not folder_link_store.get_link(link_id):
        raise HTTPException(status_code=404, detail="Folder link not found")
    try:
        link = folder_link_store.update_link(link_id, **req.dict(exclude_none=True))
    except FolderLinkPathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    titles = await _notebook_titles()
    return await _decorate(link, titles)


@router.delete("/links/{link_id}")
async def delete_link(link_id: str):
    """Unlink only. Sources already ingested stay in the notebook — stopping a
    watch is not the same as undoing what it taught."""
    if not folder_link_store.delete_link(link_id):
        raise HTTPException(status_code=404, detail="Folder link not found")
    # Review items belong to the link, not the notebook — an unlinked folder
    # must not leave cards in the queue pointing at a watch that no longer exists.
    try:
        from storage.smart_folder_store import smart_folder_store
        smart_folder_store.forget_link(link_id)
    except Exception as e:
        logger.warning(f"[folders] could not clear review queue for {link_id}: {e}")
    return {"ok": True}


@router.post("/links/{link_id}/scan")
async def scan_now(link_id: str):
    from services.folder_watcher import folder_watcher
    if not folder_link_store.get_link(link_id):
        raise HTTPException(status_code=404, detail="Folder link not found")
    report = await folder_watcher.scan_link(link_id)
    return report.to_dict()


@router.get("/links/{link_id}/preview")
async def preview(link_id: str, limit: int = 20):
    """Dry run: exactly what the next scan would do, and nothing else.

    Nothing is read beyond a stat, nothing is ingested, nothing is recorded.
    """
    from services.folder_watcher import folder_watcher
    link = folder_link_store.get_link(link_id)
    if not link:
        raise HTTPException(status_code=404, detail="Folder link not found")
    try:
        cands = await asyncio.to_thread(folder_watcher.discover, link)
    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=400,
                            detail=folder_watcher._access_error(link["path"], e))
    actionable = [c for c in cands if c.action in ("ingest", "changed")]
    return {
        "link_id": link_id,
        "path": link["path"],
        "new_files": len(actionable),
        "other": len(cands) - len(actionable),
        "files": [
            {"name": c.name, "size": c.size, "action": c.action, "reason": c.reason}
            for c in cands[:limit]
        ],
    }


class PreviewPathRequest(BaseModel):
    path: str
    patterns: Optional[List[str]] = None
    recursive: bool = False


@router.post("/preview-path")
async def preview_path(req: PreviewPathRequest):
    """Look at a folder BEFORE linking it: is it readable, and what's in it?

    This is where a macOS permission problem surfaces — at the moment the user
    picks the folder, with an actionable message, rather than silently at 3am.
    """
    from services.folder_watcher import folder_watcher
    try:
        resolved = validate_folder_path(req.path)
    except FolderLinkPathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    fake = {"id": "__preview__", "path": str(resolved),
            "patterns": req.patterns or DEFAULT_PATTERNS,
            "recursive": req.recursive, "notebook_id": None}
    try:
        cands = await asyncio.to_thread(folder_watcher.discover, fake)
    except (OSError, PermissionError) as e:
        raise HTTPException(status_code=400,
                            detail=folder_watcher._access_error(str(resolved), e))
    actionable = [c for c in cands if c.action in ("ingest", "changed")]
    return {
        "path": str(resolved),
        "display_path": str(resolved).replace(str(Path.home()), "~", 1),
        "readable": True,
        "matching_files": len(actionable),
        "sample": [c.name for c in actionable[:8]],
    }


@router.get("/ledger/{link_id}")
async def ledger(link_id: str, limit: int = 200):
    """What this folder has actually done — the audit trail behind the counts."""
    if not folder_link_store.get_link(link_id):
        raise HTTPException(status_code=404, detail="Folder link not found")
    rows = folder_link_store.seen_map(link_id)
    items = sorted(rows.values(), key=lambda r: r.get("ingested_at") or r.get("first_seen_at") or "",
                   reverse=True)[:limit]
    return {"link_id": link_id, "entries": [
        {"name": os.path.basename(r["abs_path"]), "path": r["abs_path"],
         "status": r["status"], "error": r.get("error"),
         "source_id": r.get("source_id"), "ingested_at": r.get("ingested_at")}
        for r in items
    ]}


# ══════════════════════════════════════════════════════════════════════════
# Smart Folders — the review queue and the rules
#
# The endpoints below are arranged around one invariant: nothing here ingests
# a file except in response to a human decision (`/pending/{id}/approve`) or a
# rule a human wrote. There is no "auto-approve above X" endpoint, because
# there is no confidence level at which a 1:1 transcript should file itself.
# ══════════════════════════════════════════════════════════════════════════

class ApproveRequest(BaseModel):
    notebook_id: Optional[str] = None     # None => accept the suggestion
    # Optional third button: "and always route these in future". The user
    # chooses the SCOPE, so the rule is something they can read back later.
    rule_scope: Optional[str] = None      # "participants" | "topics" | "both"


class RuleRequest(BaseModel):
    notebook_id: str
    participants: Optional[List[str]] = None
    topics: Optional[List[str]] = None


@router.get("/pending")
async def list_pending(link_id: Optional[str] = None,
                       notebook_id: Optional[str] = None):
    """The review queue. Central when unscoped; per-notebook when given one."""
    from storage.smart_folder_store import smart_folder_store
    items = smart_folder_store.list_pending(link_id=link_id, notebook_id=notebook_id)
    titles = await _notebook_titles()
    return {
        "items": items,
        "count": len(items),
        "total_pending": smart_folder_store.count_pending(),
        "notebooks": [{"id": k, "title": v} for k, v in sorted(
            titles.items(), key=lambda kv: kv[1].lower())],
        # Repeated no-match for one person is the system noticing a
        # relationship the user has not made a notebook for yet.
        "suggested_notebooks": smart_folder_store.unrouted_by_participant(),
    }


@router.post("/pending/{item_id}/approve")
async def approve_pending(item_id: str, req: ApproveRequest):
    """File this recording where the user said, and learn from the choice."""
    from services.folder_watcher import folder_watcher
    from storage.smart_folder_store import smart_folder_store

    item = smart_folder_store.get_pending(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Review item not found")
    if item["status"] != "pending":
        raise HTTPException(status_code=409,
                            detail=f"Already {item['status']}.")

    notebook_id = req.notebook_id or item.get("suggested_id")
    if not notebook_id:
        raise HTTPException(
            status_code=400,
            detail="No notebook chosen and nothing was suggested — pick one.")

    result = await folder_watcher.ingest_approved(
        link_id=item["link_id"], abs_path=item["abs_path"],
        notebook_id=notebook_id, origin="smart_folder",
    )
    if not result.get("ok"):
        smart_folder_store.resolve_pending(item_id, status="pending",
                                           error=result.get("error"))
        raise HTTPException(status_code=500, detail=result.get("error", "Ingest failed"))

    smart_folder_store.resolve_pending(item_id, status="approved",
                                       notebook_id=notebook_id)

    # Learn from the choice. This moves the SUGGESTION next time; it never
    # grants permission — that is what a rule is for.
    corrected = notebook_id != item.get("suggested_id")
    for person in (item.get("participants") or [])[:1]:
        try:
            from agents.correspondent import _record_sender_routing
            _record_sender_routing(person, notebook_id)
        except Exception as e:
            logger.debug(f"[folders] routing bias not recorded: {e}")

    rule = None
    if req.rule_scope:
        try:
            rule = smart_folder_store.create_rule(
                notebook_id=notebook_id,
                participants=(item.get("participants") or [])
                    if req.rule_scope in ("participants", "both") else None,
                topics=(item.get("topics") or [])
                    if req.rule_scope in ("topics", "both") else None,
                created_from=item_id,
            )
        except ValueError as e:
            # The ingest already happened and is correct; only the standing
            # authorisation could not be written. Say so rather than failing.
            logger.warning(f"[folders] rule not created: {e}")

    return {"ok": True, "notebook_id": notebook_id, "corrected": corrected,
            "source_id": result.get("source_id"), "rule": rule}


@router.post("/pending/{item_id}/dismiss")
async def dismiss_pending(item_id: str):
    """Not this one. The file stays on disk, untouched, and is not re-queued."""
    from storage.smart_folder_store import smart_folder_store
    if not smart_folder_store.get_pending(item_id):
        raise HTTPException(status_code=404, detail="Review item not found")
    smart_folder_store.resolve_pending(item_id, status="dismissed")
    return {"ok": True}


@router.get("/rules")
async def list_rules():
    """Standing authorisations, in plain English, with what they actually did."""
    from storage.smart_folder_store import smart_folder_store
    titles = await _notebook_titles()
    rules = smart_folder_store.list_rules()
    for r in rules:
        r["notebook_title"] = titles.get(r["notebook_id"], "Unknown notebook")
    return {"rules": rules}


@router.post("/rules")
async def create_rule(req: RuleRequest):
    from storage.smart_folder_store import smart_folder_store
    try:
        return smart_folder_store.create_rule(
            notebook_id=req.notebook_id,
            participants=req.participants, topics=req.topics,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/rules/{rule_id}")
async def toggle_rule(rule_id: str, enabled: bool = True):
    from storage.smart_folder_store import smart_folder_store
    rule = smart_folder_store.set_rule_enabled(rule_id, enabled)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    from storage.smart_folder_store import smart_folder_store
    if not smart_folder_store.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"ok": True}
