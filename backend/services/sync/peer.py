"""Mac-to-Mac: the listeners and the protocol (LB-12b).

Two TLS listeners on this Mac, both separate from the app's loopback API:

  * **sync** (`settings.sync_port`, 0.0.0.0) — mutual TLS 1.3; the trust store
    is exactly the paired Macs' certificates, so an unpaired Mac cannot finish
    the handshake. Every request is ALSO signed with the sender's device key
    (the server framework cannot say which client certificate connected, and
    the signature binds each request to one pinned device).
  * **pairing** (`sync_port + 1`) — server-auth only, open only while the user
    has opened a pairing window. Nothing is trusted here: it exchanges
    certificates, and the 6-digit code both screens show is what the user
    compares before either side pins the other.

The initiating Mac pulls AND pushes, so the outbound-only work Mac (D8)
converges both ways without ever accepting a connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request

from services.sync import engine, genesis, identity, progress, runtime, store

logger = logging.getLogger(__name__)

PAIRING_WINDOW_S = 300
PAGE = 400
PREVIEW_PAGE = 100_000


def sync_port() -> int:
    from config import settings

    return int(getattr(settings, "sync_port", 47600))


# ── server: the protocol ─────────────────────────────────────────────────────

sync_app = FastAPI(title="LocalBook sync", docs_url=None, redoc_url=None, openapi_url=None)


async def _verified(request: Request) -> Dict[str, Any]:
    """The sender, if it is a paired, unrevoked Mac and the signature holds."""
    body = await request.body()
    dev_id = request.headers.get("X-LB-Device", "")
    d = store.device(dev_id)
    if not d or d.get("revoked_at"):
        raise HTTPException(403, "not a paired Mac")
    if not identity.verify_signature(d["cert_pem"], request.method, request.url.path,
                                     request.headers.get("X-LB-Time", "0"), body,
                                     request.headers.get("X-LB-Sig", "")):
        raise HTTPException(403, "bad signature")
    store.update_device(dev_id, last_seen=time.time(),
                        host=d.get("host") or (request.client.host if request.client else None))
    return {"device": d, "body": json.loads(body or b"{}")}


def _check_enabled():
    if not store.enabled():
        raise HTTPException(503, "sync is off on this Mac")


@sync_app.post("/sync/hello")
async def hello(request: Request):
    _check_enabled()
    v = await _verified(request)
    mine = runtime.ledger_head()
    theirs = int(v["body"].get("head", -1))
    if theirs != mine:
        raise HTTPException(409, json.dumps({"reason": "version", "head": mine, "peer_head": theirs}))
    _note_models(v["device"], v["body"].get("models"))
    return {"device_id": identity.device_id(), "name": identity.device_name(),
            "proto": identity.PROTOCOL, "head": mine, "models": models(),
            "vv": await asyncio.to_thread(runtime.vvs)}


MODEL_ROLES = ("main_model", "fast_model", "vision_model", "image_model", "embedding_model")


def models() -> Dict[str, str]:
    """This Mac's resolved model per role. Exchanged in hello so two Macs that are meant to
    run the same models can see when they do not (a stale prefs override, a half download)."""
    from config import settings

    return {k: getattr(settings, k, "") or "" for k in MODEL_ROLES}


def model_mismatch(theirs: Optional[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """role → {here, there} for every role whose model differs. An older peer that sends
    no models compares as nothing to report, not as a mismatch."""
    if not theirs:
        return {}
    mine = models()
    return {k.replace("_model", ""): {"here": mine[k], "there": theirs.get(k, "")}
            for k in MODEL_ROLES if theirs.get(k) and theirs[k] != mine[k]}


def _note_models(d: Dict[str, Any], theirs: Optional[Dict[str, str]]) -> None:
    """Record (both ends of every hello) whether the peer runs different models. A warning,
    not a refusal: records still merge correctly, but each Mac embeds its own index, so a
    different embedder means the same question retrieves differently on each Mac."""
    diff = model_mismatch(theirs)
    store.put(f"model_mismatch:{d['device_id']}", diff)
    if diff:
        logger.warning("[sync] %s runs different models: %s", d.get("name") or d["device_id"], diff)


def _missing_db(db: str) -> bool:
    return not (runtime.data_dir() / runtime.DB_FILES[db]).exists()


def _export(db: str, vv: Dict[str, int], limit: int) -> Dict[str, Any]:
    if _missing_db(db):
        return {"versions": [], "more": False, "vv": {}}
    with runtime.engine_lock:
        r = runtime.replica(db)
        try:
            return engine.export(r, vv, limit=limit)
        finally:
            r.conn.close()


def _apply(db: str, page: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    if _missing_db(db):
        # Nothing to merge into yet (e.g. Curator has never run on this Mac); the
        # peer keeps these changes and offers them again next time.
        return {"inserted": 0, "updated": 0, "deleted": 0, "conflicts": [], "tables": {}, "vv": {}}
    with runtime.engine_lock:
        r = runtime.replica(db)
        try:
            rep = engine.apply(r, page, dry_run=dry_run)
            rep["vv"] = engine.vv(r.conn)
            return rep
        finally:
            r.conn.close()


@sync_app.post("/sync/pull")
async def pull(request: Request):
    _check_enabled()
    v = await _verified(request)
    b = v["body"]
    from services.sync import retention
    retention.record_acked(v["device"]["device_id"], {b["db"]: b.get("vv") or {}})
    page = await asyncio.to_thread(_export, b["db"], b.get("vv") or {},
                                   min(int(b.get("limit", PAGE)), PREVIEW_PAGE))
    if not b.get("dry_run"):
        _count_incoming(v["device"], "send", page)
    return page


def _count_incoming(d: Dict[str, Any], phase: str, page: Dict[str, Any]) -> None:
    """Show, on THIS Mac, that the other Mac is moving changes (it drives the run)."""
    n = len(page.get("versions") or [])
    if not n:
        return
    run = progress.incoming(d["device_id"], d.get("name"))
    run.step(phase, unit="changes")
    run.advance(n)
    rest = int(page.get("remaining") or 0) - n
    if rest > 0 and run.total < run.done + rest:
        run.add_total(run.done + rest - run.total)


@sync_app.post("/sync/pending")
async def pending(request: Request):
    """How many changes the caller lacks, per database — the bar's denominator."""
    _check_enabled()
    v = await _verified(request)
    vvs = v["body"].get("vv") or {}
    return {"pending": await asyncio.to_thread(_pending_all, vvs)}


def _pending_all(vvs: Dict[str, Dict[str, int]]) -> Dict[str, int]:
    out = {}
    for db in runtime.DB_FILES:
        if _missing_db(db):
            continue
        with runtime.engine_lock:
            r = runtime.replica(db)
            try:
                out[db] = engine.pending(r, vvs.get(db) or {})
            finally:
                r.conn.close()
    return out


@sync_app.post("/sync/push")
async def push(request: Request):
    _check_enabled()
    v = await _verified(request)
    b = v["body"]
    dry = bool(b.get("dry_run"))
    if not dry and not store.get("first_apply_backup"):
        # The spec: an LB-10 backup precedes each Mac's first merge.
        await asyncio.to_thread(first_apply_backup)
    rep = await asyncio.to_thread(_apply, b["db"], b["page"], dry)
    if not dry:
        _count_incoming(v["device"], "receive", b["page"])
        # The other Mac's user pressed Apply after a preview: from now on this
        # Mac initiates too (it is how a seed starts pulling from a joiner).
        if v["device"].get("mode") != "live":
            store.update_device(v["device"]["device_id"], mode="live")
        await _after_apply(rep, b["db"])
    return rep


@sync_app.post("/sync/genesis-index")
async def genesis_index(request: Request):
    _check_enabled()
    await _verified(request)
    r = runtime.replica("main")
    try:
        return genesis.index(r.conn)
    finally:
        r.conn.close()


# ── blobs (phase E) ──────────────────────────────────────────────────────────


@sync_app.post("/sync/blobs/missing")
async def blobs_missing(request: Request):
    """Which of these files this Mac does not have."""
    _check_enabled()
    v = await _verified(request)
    from services.sync import blobs
    return {"missing": await asyncio.to_thread(blobs.missing, list(v["body"].get("paths") or []))}


@sync_app.post("/sync/blob/get")
async def blob_get(request: Request):
    _check_enabled()
    v = await _verified(request)
    from services.sync import blobs
    try:
        return await asyncio.to_thread(blobs.read_chunk, v["body"]["path"], int(v["body"].get("offset", 0)))
    except (blobs.BlobPathError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc))


@sync_app.post("/sync/blob/put")
async def blob_put(request: Request):
    _check_enabled()
    v = await _verified(request)
    b = v["body"]
    run = progress.incoming(v["device"]["device_id"], v["device"].get("name"))
    run.step("files", unit="files")
    run.advance(0, detail=f"{b.get('path', '').split('/')[-1]} — "
                          f"{_mb(int(b.get('offset', 0)))} of {_mb(int(b.get('total', 0)))}")
    from services.sync import blobs
    try:
        return await asyncio.to_thread(blobs.write_chunk, b["path"], int(b["offset"]), b["data"],
                                       int(b["total"]), b["sha256"])
    except blobs.BlobPathError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


def first_apply_backup() -> Dict[str, Any]:
    """An LB-10 backup before this Mac's first merge — to the backup folder
    the user chose when turning sync on."""
    from services import backup_scheduler, backup_service

    dest = backup_scheduler.configured_destination()
    if not dest:
        raise HTTPException(409, "set a backup folder on this Mac before its first sync")
    out = backup_service.create_backup(dest, include_blobs=False)
    path = str(getattr(out, "path", "") or getattr(out, "archive", "") or "")
    store.put("first_apply_backup", {"at": time.time(), "path": path})
    return {"path": path}


def drop_document_caches() -> None:
    """Settings and core memory are cached in memory: after they change underneath the
    app (another Mac's sync, or a conflict resolved here), drop the copies."""
    try:
        from storage.memory_store import memory_store
        memory_store.invalidate_core_memory_cache()
    except Exception as exc:
        logger.debug("[sync] core memory cache: %s", exc)
    try:
        from agents.curator import curator
        curator.reload_config()
    except Exception as exc:
        logger.debug("[sync] curator config: %s", exc)
    try:
        from agents.collector import _collector_registry
        _collector_registry.clear()          # rebuilt with the synced config on next use
    except Exception as exc:
        logger.debug("[sync] collector registry: %s", exc)


def _changed(report: Dict[str, Any], table: str) -> bool:
    return bool((report.get("tables") or {}).get(table, {}).get("changed"))


async def _after_apply(report: Dict[str, Any], db: str = "main") -> None:
    """What other Macs' changes require here, after they are committed:
    derived indexes rebuilt (12j) and in-memory caches dropped."""
    try:
        from services.enrichment_jobs import EnrichmentJob, JobTier
        from services.enrichment_worker import enrichment_worker
    except Exception as exc:
        logger.warning("[sync] post-apply skipped: %s", exc)
        return

    if db == "main" and (report.get("removed_notebooks") or report.get("removed_audio")
                         or report.get("removed_video")):
        await asyncio.to_thread(_clean_removed, report)

    if db == "main":
        from services.sync import indexer

        # Each Mac embeds its own index. A user-started run indexes inside itself
        # (with progress); anything else — the other Mac pushing, the background
        # loop — gets its own visible indexing run shortly after.
        if indexer.note(report) and not progress.active("initiated"):
            indexer.kick()
        changed = [t for t, st in (report.get("tables") or {}).items() if st.get("changed")]
        if changed:
            # The open screens hold the old lists; tell them what changed (a UI reload
            # used to be the only way to see what sync brought in).
            try:
                from api.constellation_ws import broadcast_update
                await broadcast_update("sync_applied", {"tables": changed})
            except Exception as exc:
                logger.debug("[sync] ui notify: %s", exc)

    if db == "main" and _changed(report, "documents"):
        drop_document_caches()

    if db == "recall" and _changed(report, "archival_records"):
        async def _reconcile():
            import asyncio as _a

            from storage import archival_records
            from storage.memory_store import memory_store
            out = await _a.to_thread(archival_records.reconcile, memory_store)
            logger.info("[sync] archival index reconciled: %s", out)

        enrichment_worker.enqueue(EnrichmentJob(key="sync-archival-reconcile", tier=JobTier.DAYDREAM,
                                                factory=_reconcile, label="memory index after sync"))


def _clean_removed(report: Dict[str, Any]) -> None:
    """Another Mac deleted these: remove what the rows pointed at outside the database —
    exactly what api/notebooks.delete_notebook removes on the deleting Mac, and only
    that (media by the deleted rows' own ids; a deleted notebook's folder, vector table
    and derived entries). Each step is best-effort; a failure is logged, never raised."""
    import shutil

    from api.notebooks import remove_media_files

    media = {"audio": list(report.get("removed_audio") or []),
             "video": list(report.get("removed_video") or [])}
    try:
        n = remove_media_files(media)
        if n:
            logger.info("[sync] removed %d media file(s) deleted on another Mac", n)
    except Exception as exc:
        logger.warning("[sync] media cleanup: %s", exc)
    gone = [nb for nb in (report.get("removed_notebooks") or []) if nb]
    if not gone:
        return
    from services import rag_storage
    for nb in gone:
        try:
            rag_storage.drop_notebook_table(nb)
        except Exception as exc:
            logger.warning("[sync] vector table for %s: %s", nb, exc)
        try:
            d = runtime.data_dir() / "notebooks" / str(nb)
            if d.is_dir() and d.resolve().parent == (runtime.data_dir() / "notebooks").resolve():
                shutil.rmtree(d)
        except Exception as exc:
            logger.warning("[sync] notebook folder for %s: %s", nb, exc)
        try:
            from agents.collector import clear_collector_cache
            clear_collector_cache(nb)
        except Exception:
            pass
    try:
        from storage.database import get_db
        live = {r[0] for r in get_db().get_connection().execute("SELECT id FROM notebooks")}
        from services.community_detection import community_detector
        from services.entity_extractor import entity_extractor
        from services.entity_graph import entity_graph
        entity_extractor.reconcile_notebooks(live)
        entity_graph.reconcile_notebooks(live)
        community_detector.reconcile_notebooks(live)
    except Exception as exc:
        logger.warning("[sync] derived-store reconcile: %s", exc)
    logger.info("[sync] cleaned up %d notebook(s) deleted on another Mac", len(gone))


# ── server: pairing ──────────────────────────────────────────────────────────

pair_app = FastAPI(title="LocalBook pairing", docs_url=None, redoc_url=None, openapi_url=None)


@pair_app.post("/pair/hello")
async def pair_hello(request: Request):
    if float(store.get("pairing_open_until", 0)) < time.time():
        raise HTTPException(403, "pairing is not open on this Mac")
    b = json.loads(await request.body() or b"{}")
    theirs = identity.fingerprint(b["cert_pem"])
    mine_pem = identity.cert_pem()
    store.add_pairing({
        "id": uuid.uuid4().hex, "direction": "incoming", "device_id": b["device_id"],
        "name": b.get("name"), "cert_pem": b["cert_pem"], "fingerprint": theirs,
        "sas": identity.sas(identity.fingerprint(mine_pem), theirs),
        "host": request.client.host if request.client else None, "port": b.get("port"),
        "recovery_pub": b.get("recovery_pub"), "created_at": time.time(),
    })
    return {"device_id": identity.device_id(), "name": identity.device_name(),
            "cert_pem": mine_pem, "recovery_pub": _recovery_pub()}


def _recovery_pub() -> Optional[str]:
    try:
        from services import keyvault

        p = keyvault._recovery_pub_path()
        return p.read_text().strip() if p.exists() else None
    except Exception:
        return None


# ── listeners ────────────────────────────────────────────────────────────────

_servers: Dict[str, Any] = {}


async def _serve(name: str, app, port: int, ctx) -> None:
    import uvicorn

    await stop(name)
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning",
                            lifespan="off", access_log=False)
    config.load()
    config.ssl = ctx                        # our context: pinned peers, TLS 1.3 only
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    task = asyncio.create_task(server.serve(), name=f"sync-listener-{name}")
    _servers[name] = (server, task)
    for _ in range(50):
        if server.started or task.done():
            break
        await asyncio.sleep(0.05)
    if task.done() and task.exception():
        raise task.exception()


async def stop(name: str) -> None:
    entry = _servers.pop(name, None)
    if entry:
        server, task = entry
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5)
        except Exception:
            task.cancel()


async def start_sync_listener() -> None:
    """(Re)start with the current trust store — called on enable, pair, revoke."""
    pems = [d["cert_pem"] for d in store.devices()]
    ctx = await asyncio.to_thread(identity.server_context, pems, True)
    await _serve("sync", sync_app, sync_port(), ctx)


async def open_pairing_window() -> float:
    until = time.time() + PAIRING_WINDOW_S
    store.put("pairing_open_until", until)
    ctx = await asyncio.to_thread(identity.server_context, [], False)
    await _serve("pair", pair_app, sync_port() + 1, ctx)

    async def _close_later():
        await asyncio.sleep(PAIRING_WINDOW_S)
        if float(store.get("pairing_open_until", 0)) <= time.time():
            await stop("pair")

    asyncio.create_task(_close_later())
    return until


def listening() -> Dict[str, bool]:
    return {n: bool(s.started) for n, (s, _) in _servers.items()}


# ── client ───────────────────────────────────────────────────────────────────


async def request_pairing(host: str, port: Optional[int] = None) -> Dict[str, Any]:
    """Joiner side: say hello to a Mac with its pairing window open. Returns the
    code to compare; nothing is pinned until the user confirms."""
    import httpx

    port = int(port or sync_port())
    ctx = await asyncio.to_thread(identity.client_context, None)
    mine = identity.cert_pem()
    body = {"device_id": identity.device_id(), "name": identity.device_name(),
            "cert_pem": mine, "recovery_pub": _recovery_pub(), "port": sync_port()}
    async with httpx.AsyncClient(verify=ctx, timeout=20) as c:
        r = await c.post(f"https://{host}:{port + 1}/pair/hello", json=body)
        if r.status_code != 200:
            raise RuntimeError(r.json().get("detail") if r.headers.get("content-type", "").startswith(
                "application/json") else r.text)
        ssl_obj = r.extensions["network_stream"].get_extra_info("ssl_object")
        seen = identity.fingerprint_der(ssl_obj.getpeercert(binary_form=True))
    theirs = r.json()
    if identity.fingerprint(theirs["cert_pem"]) != seen:
        raise RuntimeError("the certificate in the reply is not the one the connection used")
    req = {"id": uuid.uuid4().hex, "direction": "outgoing", "device_id": theirs["device_id"],
           "name": theirs.get("name"), "cert_pem": theirs["cert_pem"], "fingerprint": seen,
           "sas": identity.sas(identity.fingerprint(mine), seen), "host": host, "port": port,
           "recovery_pub": theirs.get("recovery_pub"), "created_at": time.time()}
    store.add_pairing(req)
    return req


class Session:
    """One contact with one paired Mac, as the initiator."""

    def __init__(self, d: Dict[str, Any], run=None):
        self.d = d
        self.run = run or progress.NULL

    async def __aenter__(self):
        import httpx

        ctx = await asyncio.to_thread(identity.client_context, self.d["cert_pem"])
        self.client = httpx.AsyncClient(base_url=f"https://{self.d['host']}:{self.d.get('port') or sync_port()}",
                                        verify=ctx, timeout=httpx.Timeout(900, connect=10))
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()

    async def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        raw = json.dumps(body, separators=(",", ":")).encode()
        r = await self.client.post(path, content=raw, headers={
            "Content-Type": "application/json", **identity.sign_headers("POST", path, raw)})
        if r.status_code == 409 and path == "/sync/hello":
            detail = json.loads(r.json().get("detail", "{}"))
            raise VersionSkew(self.d.get("name") or self.d["device_id"], detail)
        if r.status_code != 200:
            raise RuntimeError(f"{path} → {r.status_code}: {r.text[:200]}")
        return r.json()

    async def hello(self) -> Dict[str, Any]:
        h = await self.post("/sync/hello", {"device_id": identity.device_id(),
                                            "head": runtime.ledger_head(), "proto": identity.PROTOCOL,
                                            "models": models()})
        _note_models(self.d, h.get("models"))
        from services.sync import retention
        retention.record_acked(self.d["device_id"], h.get("vv") or {})
        return h

    async def pending(self) -> Dict[str, Dict[str, int]]:
        """Changes waiting in each direction, per database: {"in": {...}, "out": {...}}.
        An older peer without /sync/pending gives no totals; the bar then grows as
        pages report what remains."""
        mine = await asyncio.to_thread(runtime.vvs)
        try:
            theirs = (await self.post("/sync/pending", {"vv": mine}))["pending"]
        except Exception:
            theirs = {}
        return {"in": theirs}

    async def pull_all(self, db: str, dry_run: bool = False) -> Dict[str, Any]:
        total = {"inserted": 0, "updated": 0, "deleted": 0, "conflicts": 0, "tables": {}}
        for _ in range(100_000):
            self.run.check()
            vv = (await asyncio.to_thread(runtime.vvs))[db]
            page = await self.post("/sync/pull", {"db": db, "vv": vv, "dry_run": dry_run,
                                                  "limit": PREVIEW_PAGE if dry_run else PAGE})
            rep = await asyncio.to_thread(_apply, db, page, dry_run)
            self.run.advance(len(page.get("versions") or []))
            _add(total, rep)
            if not dry_run:
                await _after_apply(rep, db)
            if not page.get("more") or dry_run:
                return total
        raise RuntimeError("pull did not finish")

    async def fetch_blobs(self) -> Dict[str, int]:
        """Download every audio/video file local rows point at but this Mac lacks."""
        from services.sync import blobs

        def _need():
            r = runtime.replica("main")
            try:
                return blobs.missing(blobs.referenced(r.conn))
            finally:
                r.conn.close()

        got = failed = 0
        need = await asyncio.to_thread(_need)
        self.run.add_total(len(need))
        for rel in need:
            self.run.check()
            name = rel.split("/")[-1]
            try:
                offset = await asyncio.to_thread(blobs.partial_offset, rel)
                digest, total = None, None
                if offset:
                    head = await self.post("/sync/blob/get", {"path": rel, "offset": 0})
                    digest, total = head["sha256"], head["total"]
                while True:
                    c = await self.post("/sync/blob/get", {"path": rel, "offset": offset})
                    digest = digest or c["sha256"]
                    total = c["total"]
                    res = await asyncio.to_thread(blobs.write_chunk, rel, offset, c["data"], total, digest)
                    offset = res["next"]
                    self.run.advance(0, detail=f"{name} — {_mb(offset)} of {_mb(total)}")
                    if res["done"]:
                        got += 1
                        break
                    self.run.check()
            except progress.Cancelled:
                raise
            except Exception as exc:
                failed += 1
                logger.info("[sync] blob %s not fetched yet: %s", rel, exc)
            self.run.advance(1)
        return {"fetched": got, "pending": failed}

    async def send_blobs(self) -> Dict[str, int]:
        """Upload what the peer lacks among the files this Mac has."""
        import base64

        from services.sync import blobs

        def _have():
            r = runtime.replica("main")
            try:
                refs = blobs.referenced(r.conn)
            finally:
                r.conn.close()
            return [x for x in refs if x not in blobs.missing(refs)]

        have = await asyncio.to_thread(_have)
        if not have:
            return {"sent": 0}
        need = (await self.post("/sync/blobs/missing", {"paths": have}))["missing"]
        self.run.add_total(len(need))
        sent = 0
        for rel in need:
            self.run.check()
            name = rel.split("/")[-1]
            try:
                path = blobs.safe_path(rel)
                total = path.stat().st_size
                digest = await asyncio.to_thread(blobs.sha256, path)
                offset = 0
                with open(path, "rb") as f:
                    while True:
                        f.seek(offset)
                        data = f.read(blobs.CHUNK)
                        res = await self.post("/sync/blob/put", {
                            "path": rel, "offset": offset, "total": total, "sha256": digest,
                            "data": base64.b64encode(data).decode("ascii")})
                        offset = res["next"]
                        self.run.advance(0, detail=f"{name} — {_mb(offset)} of {_mb(total)}")
                        if res["done"]:
                            sent += 1
                            break
                        self.run.check()
            except progress.Cancelled:
                raise
            except Exception as exc:
                logger.info("[sync] blob %s not sent yet: %s", rel, exc)
            self.run.advance(1)
        return {"sent": sent}

    async def push_all(self, db: str, peer_vv: Dict[str, int], dry_run: bool = False) -> Dict[str, Any]:
        total = {"inserted": 0, "updated": 0, "deleted": 0, "conflicts": 0, "tables": {}}
        for _ in range(100_000):
            self.run.check()
            page = await asyncio.to_thread(_export, db, peer_vv, PREVIEW_PAGE if dry_run else PAGE)
            if not page["versions"] and not page.get("vv"):
                return total
            rep = await self.post("/sync/push", {"db": db, "page": page, "dry_run": dry_run})
            self.run.advance(len(page["versions"]))
            _add(total, rep)
            peer_vv = rep.get("vv") or peer_vv
            if not dry_run and rep.get("vv"):
                from services.sync import retention
                retention.record_acked(self.d["device_id"], {db: rep["vv"]})
            if not page.get("more") or dry_run:
                return total
        raise RuntimeError("push did not finish")


class VersionSkew(RuntimeError):
    def __init__(self, name: str, detail: Dict[str, Any]):
        super().__init__(f"Update LocalBook on {name if detail.get('peer_head', 0) > detail.get('head', 0) else 'this Mac'} "
                         f"to resume sync (schema {detail.get('head')} vs {detail.get('peer_head')})")
        self.detail = detail


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB"


def _add(total: Dict[str, Any], rep: Dict[str, Any]) -> None:
    for k in ("inserted", "updated", "deleted"):
        total[k] += int(rep.get(k, 0))
    total["conflicts"] += len(rep.get("conflicts", [])) if isinstance(rep.get("conflicts"), list) \
        else int(rep.get("conflicts", 0))
    for t, s in (rep.get("tables") or {}).items():
        cur = total["tables"].setdefault(t, {"in": 0, "changed": 0})
        cur["in"] += s.get("in", 0)
        cur["changed"] += s.get("changed", 0)
