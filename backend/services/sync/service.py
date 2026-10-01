"""What the Settings screen and the background loop call (LB-12).

    enable / disable          the per-Mac flag (D11) + the listener; off = kill switch
    open_pairing / pair_with  the two halves of pairing; confirm_pairing pins
    preview / apply           first contact: always a dry run first (user
                              decision 2026-09-30), Apply converges it
    sync_now / loop           ongoing sync with every live peer
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any, Dict, List, Optional

from services.sync import genesis, identity, peer, runtime, store

logger = logging.getLogger(__name__)

SCHEDULE_ID = "sync"
DEFAULT_INTERVAL = 60
_loop_task: Optional[asyncio.Task] = None
_busy: Dict[str, bool] = {}


# ── on / off ────────────────────────────────────────────────────────────────


async def enable() -> Dict[str, Any]:
    from services import backup_scheduler

    if not backup_scheduler.configured_destination():
        raise ValueError("choose a backup folder first — a backup is taken before this Mac's first sync")
    await asyncio.to_thread(identity.cert_pem)            # key + certificate exist
    await asyncio.to_thread(runtime.install_journals)
    store.put("enabled", True)
    await peer.start_sync_listener()
    start_loop()
    return status()


async def disable() -> Dict[str, Any]:
    """The kill switch: takes effect immediately. Journals keep recording, so
    turning sync back on loses nothing."""
    store.put("enabled", False)
    await peer.stop("sync")
    await peer.stop("pair")
    return status()


async def startup() -> None:
    """At launch: resume only if this Mac had sync on."""
    if not store.enabled():
        return
    try:
        await asyncio.to_thread(runtime.install_journals)
        await peer.start_sync_listener()
        start_loop()
    except Exception as exc:
        logger.error("[sync] could not start: %s", exc)


# ── pairing ─────────────────────────────────────────────────────────────────


async def open_pairing() -> Dict[str, Any]:
    if not store.enabled():
        raise ValueError("turn sync on first")
    until = await peer.open_pairing_window()
    return {"open_until": until, "addresses": addresses(), "port": peer.sync_port()}


async def pair_with(host: str, port: Optional[int] = None) -> Dict[str, Any]:
    if not store.enabled():
        raise ValueError("turn sync on first")
    return await peer.request_pairing(host.strip(), port)


async def confirm_pairing(pairing_id: str) -> Dict[str, Any]:
    """The user saw the same code on both Macs. Pin the other Mac.

    The Mac that opened the window is the SEED for genesis (D12): the Mac that
    asked to pair adopts its ids for what they both have.
    """
    req = store.take_pairing(pairing_id)
    if not req:
        raise ValueError("that pairing request has expired — start again")
    role = "joiner" if req["direction"] == "incoming" else "seed"
    store.pin(req, role)
    _adopt_recovery_key(req.get("recovery_pub"))
    await peer.start_sync_listener()                       # the trust store changed
    return status()


def _adopt_recovery_key(pub: Optional[str]) -> None:
    """D6′: one recovery phrase for every Mac. A Mac with none takes the other's
    PUBLIC key (never a secret), so its backups open with the same phrase."""
    if not pub:
        return
    try:
        from services import keyvault

        p = keyvault._recovery_pub_path()
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(pub.strip() + "\n")
            keyvault.wrap_all()
    except Exception as exc:
        logger.warning("[sync] could not adopt the recovery key: %s", exc)


def reject_pairing(pairing_id: str) -> None:
    store.take_pairing(pairing_id)


async def revoke(device_id: str) -> Dict[str, Any]:
    store.update_device(device_id, revoked_at=time.time())
    await peer.start_sync_listener()
    return status()


# ── first contact: preview, then apply ──────────────────────────────────────


def _local_index() -> Dict[str, Any]:
    r = runtime.replica("main")
    try:
        return genesis.index(r.conn)
    finally:
        r.conn.close()


async def preview(device_id: str) -> Dict[str, Any]:
    d = _device(device_id)
    async with peer.Session(d) as s:
        await s.hello()
        report: Dict[str, Any] = {}
        if d.get("role") == "seed":                          # this Mac joins: it re-keys
            seed_index = await s.post("/sync/genesis-index", {})
            report["genesis"] = genesis.plan(await asyncio.to_thread(_local_index), seed_index)["counts"]
        report["incoming"] = await s.pull_all("main", dry_run=True)
    report.pop("vv", None)
    store.save_preview(device_id, report)
    store.update_device(device_id, last_error=None)
    return report


async def apply(device_id: str) -> Dict[str, Any]:
    """The user pressed Apply after reading the preview."""
    d = _device(device_id)
    if not store.get("first_apply_backup"):
        await asyncio.to_thread(peer.first_apply_backup)
    async with peer.Session(d) as s:
        await s.hello()
        if d.get("role") == "seed" and not store.get(f"genesis_done:{device_id}"):
            seed_index = await s.post("/sync/genesis-index", {})
            the_plan = genesis.plan(await asyncio.to_thread(_local_index), seed_index)
            await asyncio.to_thread(_rekey, the_plan)
            store.put(f"genesis_done:{device_id}", the_plan["counts"])
    store.update_device(device_id, mode="live")
    return await sync_with(device_id, user_initiated=True)


def _rekey(the_plan: Dict[str, Any]) -> None:
    with runtime.engine_lock:
        r = runtime.replica("main")
        try:
            genesis.rekey(r.conn, the_plan, runtime.data_dir())
        finally:
            r.conn.close()
        rr = runtime.replica("recall")
        try:
            genesis.rekey_recall(rr.conn, the_plan)
        finally:
            rr.conn.close()


# ── ongoing sync ────────────────────────────────────────────────────────────


async def sync_with(device_id: str, user_initiated: bool = False) -> Dict[str, Any]:
    d = _device(device_id)
    if d.get("mode") != "live":
        raise ValueError("preview and apply this Mac first")
    if _busy.get(device_id):
        return {"skipped": "already syncing"}
    _busy[device_id] = True
    try:
        async with peer.Session(d) as s:
            h = await s.hello()
            out = {}
            for db in runtime.DB_FILES:
                if not (runtime.data_dir() / runtime.DB_FILES[db]).exists():
                    continue
                incoming = await s.pull_all(db)
                outgoing = await s.push_all(db, (h.get("vv") or {}).get(db, {}))
                out[db] = {"in": incoming, "out": outgoing}
            # Files last, and only while the user is not active (D15: eager, but
            # at the lowest priority) — a skipped round is picked up by the next.
            if user_initiated or _user_idle():
                out["blobs"] = {**await s.fetch_blobs(), **await s.send_blobs()}
            else:
                out["blobs"] = {"deferred": "you are using LocalBook"}
        store.update_device(device_id, last_seen=time.time(), last_error=None)
        store.put(f"last_sync:{device_id}", {"at": time.time(), "result": out})
        return out
    except peer.VersionSkew as exc:
        store.update_device(device_id, last_error=str(exc))
        raise
    except Exception as exc:
        store.update_device(device_id, last_error=f"{type(exc).__name__}: {exc}"[:300])
        raise
    finally:
        _busy.pop(device_id, None)


def _user_idle() -> bool:
    try:
        from services import presence
        return not presence.is_active()
    except Exception:
        return True


async def sync_all() -> Dict[str, Any]:
    results = {}
    for d in store.devices():
        if d.get("mode") != "live" or not d.get("host"):
            continue
        try:
            results[d["device_id"]] = await sync_with(d["device_id"])
        except Exception as exc:
            # "Waiting for a peer" is a normal state (the MDM Mac is often the
            # only one on), not an error worth more than a line.
            results[d["device_id"]] = {"error": str(exc)[:200]}
    return results


def start_loop() -> None:
    global _loop_task
    if _loop_task and not _loop_task.done():
        return
    from utils.tasks import safe_create_task

    _loop_task = safe_create_task(_loop(), name="sync-loop")


async def _loop() -> None:
    from services.schedule_store import schedule_store

    while True:
        interval = DEFAULT_INTERVAL
        try:
            interval = schedule_store.get_interval(SCHEDULE_ID, DEFAULT_INTERVAL)
            if store.enabled() and schedule_store.is_enabled(SCHEDULE_ID):
                await asyncio.to_thread(runtime.install_journals)
                await sync_all()
        except Exception as exc:
            logger.warning("[sync] loop iteration failed: %s", exc)
        await asyncio.sleep(max(15, int(interval or DEFAULT_INTERVAL)))


# ── status ──────────────────────────────────────────────────────────────────


def addresses() -> List[str]:
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        if ip not in out and not ip.startswith("127."):
            out.insert(0, ip)
    except Exception:
        pass
    return out


def _device(device_id: str) -> Dict[str, Any]:
    d = store.device(device_id)
    if not d or d.get("revoked_at"):
        raise ValueError("no such paired Mac")
    if not d.get("host"):
        raise ValueError("this Mac's address is unknown — it has to connect first, or re-pair")
    return d


def _proposed_backup() -> Optional[str]:
    try:
        from services.encryption_setup import proposed_backup_destination
        return proposed_backup_destination()
    except Exception:
        return None


def status() -> Dict[str, Any]:
    from services import backup_scheduler

    devices = []
    for d in store.devices():
        devices.append({k: d.get(k) for k in ("device_id", "name", "host", "port", "role", "mode",
                                               "paired_at", "last_seen", "last_error")}
                       | {"fingerprint": d["fingerprint"][:16], "preview": store.preview(d["device_id"]),
                          "last_sync": store.get(f"last_sync:{d['device_id']}"),
                          "model_mismatch": store.get(f"model_mismatch:{d['device_id']}") or {}})
    try:
        c = runtime.replica("main").conn
        open_conflicts = c.execute("SELECT COUNT(*) FROM sync_conflicts WHERE status='open'").fetchone()[0]
        c.close()
    except Exception:
        open_conflicts = None
    return {
        "enabled": store.enabled(),
        "this_mac": {"device_id": identity.device_id(), "name": identity.device_name(),
                     "addresses": addresses(), "port": peer.sync_port()},
        "listening": peer.listening(),
        "pairing_open_until": store.get("pairing_open_until", 0),
        "pairing": [{k: p[k] for k in ("id", "direction", "device_id", "name", "sas", "host")}
                    for p in store.pairing_requests()],
        "devices": devices,
        "open_conflicts": open_conflicts,
        "backup_destination": str(backup_scheduler.configured_destination() or "") or None,
        "proposed_backup": _proposed_backup(),
        "first_apply_backup": store.get("first_apply_backup"),
        "schema_head": runtime.ledger_head(),
    }
