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

from services.sync import engine, genesis, identity, runtime, store

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
    return {"device_id": identity.device_id(), "name": identity.device_name(),
            "proto": identity.PROTOCOL, "head": mine,
            "vv": await asyncio.to_thread(runtime.vvs)}


def _export(db: str, vv: Dict[str, int], limit: int) -> Dict[str, Any]:
    with runtime.engine_lock:
        r = runtime.replica(db)
        try:
            return engine.export(r, vv, limit=limit)
        finally:
            r.conn.close()


def _apply(db: str, page: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
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
    return await asyncio.to_thread(_export, b["db"], b.get("vv") or {},
                                   min(int(b.get("limit", PAGE)), PREVIEW_PAGE))


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
        # The other Mac's user pressed Apply after a preview: from now on this
        # Mac initiates too (it is how a seed starts pulling from a joiner).
        if v["device"].get("mode") != "live":
            store.update_device(v["device"]["device_id"], mode="live")
        if b["db"] == "main":
            await _after_apply(rep)
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


async def _after_apply(report: Dict[str, Any]) -> None:
    """Arrived sources are re-embedded here, as dosed background jobs (12j)."""
    if not report.get("tables", {}).get("sources", {}).get("changed"):
        return
    try:
        from services.enrichment_jobs import EnrichmentJob, JobTier
        from services.enrichment_worker import enrichment_worker

        async def _reindex_all_changed():
            from api.reindex import reindex_notebook
            from storage.notebook_store import notebook_store

            for nb in await notebook_store.list():
                try:
                    await reindex_notebook(nb["id"], force=False)
                except Exception as exc:
                    logger.warning("[sync] re-index of %s after sync failed: %s", nb.get("id"), exc)

        enrichment_worker.enqueue(EnrichmentJob(key="sync-reindex", tier=JobTier.DAYDREAM,
                                                factory=_reindex_all_changed,
                                                label="re-index after sync"))
    except Exception as exc:
        logger.warning("[sync] could not queue the re-index: %s", exc)


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

    def __init__(self, d: Dict[str, Any]):
        self.d = d

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
        return await self.post("/sync/hello", {"device_id": identity.device_id(),
                                               "head": runtime.ledger_head(), "proto": identity.PROTOCOL})

    async def pull_all(self, db: str, dry_run: bool = False) -> Dict[str, Any]:
        total = {"inserted": 0, "updated": 0, "deleted": 0, "conflicts": 0, "tables": {}}
        for _ in range(100_000):
            vv = (await asyncio.to_thread(runtime.vvs))[db]
            page = await self.post("/sync/pull", {"db": db, "vv": vv,
                                                  "limit": PREVIEW_PAGE if dry_run else PAGE})
            rep = await asyncio.to_thread(_apply, db, page, dry_run)
            _add(total, rep)
            if not dry_run and db == "main":
                await _after_apply(rep)
            if not page.get("more") or dry_run:
                return total
        raise RuntimeError("pull did not finish")

    async def push_all(self, db: str, peer_vv: Dict[str, int], dry_run: bool = False) -> Dict[str, Any]:
        total = {"inserted": 0, "updated": 0, "deleted": 0, "conflicts": 0, "tables": {}}
        for _ in range(100_000):
            page = await asyncio.to_thread(_export, db, peer_vv, PREVIEW_PAGE if dry_run else PAGE)
            if not page["versions"] and not page.get("vv"):
                return total
            rep = await self.post("/sync/push", {"db": db, "page": page, "dry_run": dry_run})
            _add(total, rep)
            peer_vv = rep.get("vv") or peer_vv
            if not page.get("more") or dry_run:
                return total
        raise RuntimeError("push did not finish")


class VersionSkew(RuntimeError):
    def __init__(self, name: str, detail: Dict[str, Any]):
        super().__init__(f"Update LocalBook on {name if detail.get('peer_head', 0) > detail.get('head', 0) else 'this Mac'} "
                         f"to resume sync (schema {detail.get('head')} vs {detail.get('peer_head')})")
        self.detail = detail


def _add(total: Dict[str, Any], rep: Dict[str, Any]) -> None:
    for k in ("inserted", "updated", "deleted"):
        total[k] += int(rep.get(k, 0))
    total["conflicts"] += len(rep.get("conflicts", [])) if isinstance(rep.get("conflicts"), list) \
        else int(rep.get("conflicts", 0))
    for t, s in (rep.get("tables") or {}).items():
        cur = total["tables"].setdefault(t, {"in": 0, "changed": 0})
        cur["in"] += s.get("in", 0)
        cur["changed"] += s.get("changed", 0)
