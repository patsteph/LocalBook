"""Bring already-stored chunks inside the embedder's shared token limit.

Chunks indexed before `max_tokens` existed were embedded at full length, so on another device
(the iPhone cuts at 512) the same text would have a different vector. For every stored row whose
embedded text (template + chunk + HyDE questions) is over the limit:

  * NEAR  (≤ NEAR_TOKENS): re-embed in place. The cut drops a handful of tokens.
  * LONG  (> NEAR_TOKENS): split into pieces that fit. The first piece keeps the original
    (source_id, chunk_index) so citations and graph links still resolve; further pieces take
    new indices after the source's last. The HyDE questions stay on the first piece (they
    describe the whole chunk); the others have none until the source is re-enriched.

Runs once per (embedder, limit) at startup, in the background, behind a marker file. New
ingest never needs it (rag_chunking splits). It acts only when the index was built by the saved
embedder, never while an embedder switch is running, and it backs up each table it touches.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

MARKER = "chunk_limit_applied.json"
BACKUP_DIR = "lancedb_backup_chunklimit"
NEAR_TOKENS = 542          # embedded tokens ≤ this: re-embed in place instead of splitting
BATCH = 16

Embedder = Callable[[List[str]], Awaitable[List[List[float]]]]


def _marker_path() -> Path:
    from config import settings
    return Path(settings.data_dir) / MARKER


def _applied(model: str, limit: int) -> bool:
    try:
        m = json.loads(_marker_path().read_text())
        return m.get("model") == model and m.get("max_tokens") == limit
    except Exception:
        return False


def _fit_questions(row: Dict[str, Any], prof, count: Callable[[str], int]) -> None:
    """The piece that keeps the original chunk's questions must still fit WITH them. Pieces are cut to leave a
    fixed room for questions; when the stored questions are longer than that room the row stayed over the limit
    and was re-split to the same row at every pass. Trim the questions (never the text) until it fits."""
    from services.embedding_migration import embedded_text

    q = (row.get("synthetic_questions") or "").strip()
    while q and count(prof.format_document(embedded_text({**row, "synthetic_questions": q}))) + 2 > prof.max_tokens:
        cut = q[: int(len(q) * 0.8)]
        q = cut.rsplit(" ", 1)[0].strip() if len(cut) > 20 and " " in cut else ""
    row["synthetic_questions"] = q


def _seen_versions(model: str, limit: int) -> Dict[str, Any]:
    """Table versions the last completed pass covered (empty when the marker is for another embedder/limit or
    predates version tracking — then every table is looked at once)."""
    try:
        m = json.loads(_marker_path().read_text())
        if m.get("model") == model and m.get("max_tokens") == limit:
            return dict(m.get("versions") or {})
    except Exception:
        pass
    return {}


def plan_table(table, model: str) -> List[Dict[str, Any]]:
    """The rows of one table that are over the limit, each with how to fix it."""
    from services import embedding_profile, token_budget
    from services.embedding_migration import embedded_text

    prof = embedding_profile.for_model(model)
    count = token_budget.token_counter(model)
    budget = max(32, prof.max_tokens - token_budget.ENRICHMENT_RESERVE_TOKENS)
    out = []
    for r in table.to_arrow().to_pylist():
        if r.get("chunk_index") == -1:                       # the document summary row
            continue
        n = count(prof.format_document(embedded_text(r))) + 2
        if n <= prof.max_tokens:
            continue
        if n <= NEAR_TOKENS:
            out.append({"kind": "near", "row": r, "tokens": n})
        else:
            out.append({"kind": "split", "row": r, "tokens": n,
                        "pieces": token_budget.split_to_budget(r["text"], budget, count)})
    return out


async def apply_table(db, name: str, plan: List[Dict[str, Any]], model: str, embed: Embedder,
                      pace: Optional[Callable[[], Awaitable[None]]] = None) -> int:
    """Write one table's fixes. Returns rows written."""
    import pyarrow as pa
    from services import embedding_profile
    from services.embedding_migration import embedded_text

    from services import token_budget

    prof = embedding_profile.for_model(model)
    count = token_budget.token_counter(model)
    table = db.open_table(name)
    last: Dict[str, int] = {}
    for r in await asyncio.to_thread(lambda: table.to_arrow().to_pylist()):
        last[r["source_id"]] = max(last.get(r["source_id"], -1), r["chunk_index"])

    rows: List[Dict[str, Any]] = []
    for item in plan:
        r = item["row"]
        if item["kind"] == "near":
            rows.append(dict(r))
            continue
        for i, piece in enumerate(item["pieces"]):
            row = dict(r)
            row["text"] = piece
            row["synthetic_questions"] = r.get("synthetic_questions", "") if i == 0 else ""
            if i == 0:
                _fit_questions(row, prof, count)
            if i > 0:
                last[r["source_id"]] += 1
                row["chunk_index"] = last[r["source_id"]]
            rows.append(row)

    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        vecs = await embed([prof.format_document(embedded_text(b)) for b in batch])
        if len(vecs) != len(batch) or any(not any(v) for v in vecs):
            raise RuntimeError(f"embedder returned a bad vector for {name}")   # nothing written yet
        for b, v in zip(batch, vecs):
            b["vector"] = v
        if pace:
            await pace()

    def _write() -> None:
        for item in plan:
            r = item["row"]
            table.delete(f"source_id = '{r['source_id']}' AND chunk_index = {r['chunk_index']}")
        table.add(pa.Table.from_pylist(rows, schema=table.schema))

    await asyncio.to_thread(_write)
    return len(rows)


async def run(embed: Embedder, *, force: bool = False,
              pace: Optional[Callable[[], Awaitable[None]]] = None) -> Dict[str, Any]:
    """Fix every over-limit stored chunk once. Safe to call at every startup."""
    import lancedb
    from config import settings
    from services import embedding_migration as em, embedding_profile

    model = getattr(settings, "embedding_model", "") or ""
    prof = embedding_profile.for_model(model)
    if prof.max_tokens <= 0:
        return {"skipped": "embedder has no token limit"}
    if em.index_model() not in (None, model):
        return {"skipped": "index was built by a different embedder"}
    if em.status().get("running"):
        return {"skipped": "embedder switch in progress"}

    live = em._live_dir()
    db = await asyncio.to_thread(lambda: lancedb.connect(str(live)))
    # A one-time marker was not enough: chunks that arrive LATER (sync from a Mac with an older chunker, new
    # ingest) can be over the limit too and were never looked at again. The marker now remembers each table's
    # version, so only tables that changed since the last pass are checked.
    seen = _seen_versions(model, prof.max_tokens)
    tables = em._tables(live)
    changed = [n for n in tables if force or seen.get(n) != em._table_version(db, n)]
    if not changed:
        return {"skipped": "already applied"}
    fixed = written = 0
    backup = Path(settings.data_dir) / BACKUP_DIR
    for name in changed:
        plan = await asyncio.to_thread(lambda n=name: plan_table(db.open_table(n), model))
        if not plan:
            continue
        dest = backup / f"{name}.lance"
        if dest.exists():
            shutil.rmtree(dest)
        backup.mkdir(exist_ok=True)
        await asyncio.to_thread(shutil.copytree, live / f"{name}.lance", dest)
        written += await apply_table(db, name, plan, model, embed, pace)
        fixed += len(plan)
        logger.info("[chunk-limit] %s: %d rows over %d tokens fixed", name, len(plan), prof.max_tokens)
    result = {"model": model, "max_tokens": prof.max_tokens, "rows_fixed": fixed,
              "rows_written": written, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "versions": {n: em._table_version(db, n) for n in tables}}
    _marker_path().write_text(json.dumps(result, indent=2))
    return result


async def run_at_startup() -> None:
    """Background task for main.py — never raises, never blocks startup."""
    try:
        from services.embedding_migration import _pace
        from config import settings
        model = getattr(settings, "embedding_model", "") or ""

        async def embed(texts: List[str]) -> List[List[float]]:
            from services.llm_runtime import llm_runtime
            return await llm_runtime.embed_with_model(texts, model)

        out = await run(embed, pace=_pace)
        if out.get("rows_fixed") is not None:
            logger.info("[chunk-limit] done: %s", out)
            print(f"✂️  Chunk limit: {out['rows_fixed']} stored chunk(s) brought within {out['max_tokens']} tokens")
    except Exception as exc:
        logger.warning("[chunk-limit] skipped: %s", exc)
