"""Cursor Tier-1 — constrained LLM view-pick.

When Tier 0's deterministic routing can't confidently match a question to a view, the LLM does a much
EASIER job than writing SQL: pick ONE view from the catalog and name any dimension filters + a person.
It's a classification / slot-fill, not generation — so no table/column/join hallucination. The app
then assembles the SQL deterministically (scope defaults + person→key resolution stay app-owned).

Pure prompt-building + strict validation against the catalog; never raises → None on any failure so
answering falls through to the guarded free-SQL tier.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _view_menu(catalog: Dict[str, Any], max_dims: int = 8) -> str:
    lines: List[str] = []
    for name, vp in (catalog.get("views") or {}).items():
        dims = ", ".join((vp.get("dimensions") or [])[:max_dims]) or "—"
        person = "yes" if vp.get("role_keys") else "no"
        cat = vp.get("category") or ""
        lines.append(f"- {name} [{cat}] — dims: {dims}; person-filterable: {person}")
    return "\n".join(lines)


def _build_prompt(question: str, catalog: Dict[str, Any]) -> str:
    return (
        "Route the QUESTION to exactly ONE pre-built database VIEW from the list, and name any "
        "dimension filters it needs. Do NOT write SQL. Pick the single most specific view.\n\n"
        f'QUESTION: "{question}"\n\n'
        f"VIEWS:\n{_view_menu(catalog)}\n\n"
        "Respond with ONLY this JSON, nothing else:\n"
        '{"view": "<exact view name from the list>", "group_by": "<a dimension to group by, or null>", '
        '"filters": [{"col": "<dimension>", "value": "<value>"}], '
        '"person": "<a person NAME mentioned in the question, or null>"}'
    )


def _clean_opt(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("null", "none", "n/a", "") else None


def _validate(pick: Any, catalog: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Keep only what the catalog actually supports: a real view, group_by that is one of its
    dimensions, filters on real columns, an optional person string."""
    if not isinstance(pick, dict):
        return None
    views = catalog.get("views") or {}
    view = pick.get("view")
    if not isinstance(view, str) or view not in views:
        return None
    vp = views[view]
    cols = {str(c.get("name", "")).lower() for c in vp.get("columns", [])}
    dims = {str(d).lower() for d in vp.get("dimensions", [])}

    gb = _clean_opt(pick.get("group_by"))
    group_by = next((d for d in vp.get("dimensions", []) if d.lower() == (gb or "").lower()), None)

    filters: List[Dict[str, str]] = []
    for f in (pick.get("filters") or []):
        if isinstance(f, dict):
            col, val = str(f.get("col", "")), _clean_opt(f.get("value"))
            if col.lower() in cols and val is not None:
                filters.append({"col": col, "value": val})

    return {"view": view, "group_by": group_by, "filters": filters,
            "person": _clean_opt(pick.get("person"))}


async def pick_view(question: str, catalog: Optional[Dict[str, Any]], model: str,
                    timeout: float = 25.0) -> Optional[Dict[str, Any]]:
    """Ask the model to pick one view + slots; return the catalog-validated pick, or None."""
    if not catalog or not (catalog.get("views")):
        return None
    try:
        from services.ollama_service import ollama_service
        from utils.json_repair import robust_json_parse
        res = await ollama_service.generate(
            prompt=_build_prompt(question, catalog), model=model,
            temperature=0.1, num_predict=220, think=False, timeout=timeout)
        text = (res or {}).get("response", "") or ""
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        return _validate(robust_json_parse(m.group(0)), catalog)
    except Exception as e:
        logger.warning(f"[cursor_view_pick] failed: {type(e).__name__}: {e}")
        return None
