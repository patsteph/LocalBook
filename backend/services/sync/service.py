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

from services.sync import discovery, genesis, identity, indexer, peer, progress, runtime, store

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
    discovery.advertise(identity.device_name(), peer.sync_port(), identity.device_id())
    if not store.devices():
        # First Mac or a new one: be findable without an extra click.
        await peer.open_pairing_window()
    start_loop()
    return status()


async def disable() -> Dict[str, Any]:
    """The kill switch: takes effect immediately. Journals keep recording, so
    turning sync back on loses nothing."""
    store.put("enabled", False)
    progress.cancel()
    discovery.stop()
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
        discovery.advertise(identity.device_name(), peer.sync_port(), identity.device_id())
        start_loop()
        # Catch up on anything synced but never indexed (a restart mid-run, or a
        # build before the indexer existed — the mini's first sync).
        indexer.mark_dirty()
        indexer.kick(delay=60)
        default_collector()          # Macs paired before the setting existed
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
    default_collector()
    if role == "seed":
        # This Mac asked to pair, so it previews — by itself, as soon as the other
        # Mac confirms too. One less click, and the summary is waiting.
        from utils.tasks import safe_create_task
        safe_create_task(_auto_preview(req["device_id"]), name="sync-auto-preview")
    return status()


def default_collector() -> None:
    """If no Mac is chosen to run scheduled collections, choose the Mac that was paired
    WITH (the seed — the one that opened its pairing window, usually the always-on
    one). Every Mac computes the same answer from its own pairing records, so the
    synced setting agrees. Never overrides a choice; never raises."""
    try:
        from services.sync import roles
        if roles.collector():
            return
        devices = store.devices()
        if not devices:
            return
        seeds = [d for d in devices if d.get("role") == "seed"]
        if seeds:                                        # another Mac is the seed
            roles.set_collector(seeds[0]["device_id"], seeds[0].get("name"))
        else:                                            # this Mac is everyone's seed
            roles.set_collector(identity.device_id(), identity.device_name())
    except Exception as exc:
        logger.warning("[sync] could not choose a default collector: %s", exc)


async def _auto_preview(device_id: str, patience: float = 600) -> None:
    deadline = time.time() + patience
    store.put(f"preview_state:{device_id}", "waiting for the other Mac to confirm the code")
    while time.time() < deadline:
        try:
            await preview(device_id)
            store.put(f"preview_state:{device_id}", None)
            return
        except Exception as exc:
            if "403" not in str(exc):            # anything but "not paired there yet"
                store.put(f"preview_state:{device_id}", f"preview failed: {exc}"[:300])
                return
        await asyncio.sleep(2)
    store.put(f"preview_state:{device_id}", "the other Mac did not confirm — pair again")


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


async def apply(device_id: str, run=None) -> Dict[str, Any]:
    """The user pressed Start sync after reading the preview."""
    run = run or progress.NULL
    d = _device(device_id)
    if not store.get("first_apply_backup"):
        run.step("backup")
        await asyncio.to_thread(peer.first_apply_backup)
    async with peer.Session(d, run) as s:
        run.step("connect")
        await s.hello()
        if d.get("role") == "seed" and not store.get(f"genesis_done:{device_id}"):
            run.step("match")
            seed_index = await s.post("/sync/genesis-index", {})
            the_plan = genesis.plan(await asyncio.to_thread(_local_index), seed_index)
            await asyncio.to_thread(_rekey, the_plan)
            store.put(f"genesis_done:{device_id}", the_plan["counts"])
    store.update_device(device_id, mode="live")
    return await sync_with(device_id, user_initiated=True, run=run)


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


async def sync_with(device_id: str, user_initiated: bool = False, run=None) -> Dict[str, Any]:
    run = run or progress.NULL
    d = _device(device_id)
    if d.get("mode") != "live":
        raise ValueError("preview and apply this Mac first")
    if _busy.get(device_id):
        return {"skipped": "already syncing"}
    _busy[device_id] = True
    try:
        async with peer.Session(d, run) as s:
            run.step("connect")
            h = await s.hello()
            dbs = [db for db in runtime.DB_FILES if (runtime.data_dir() / runtime.DB_FILES[db]).exists()]
            out: Dict[str, Any] = {db: {} for db in dbs}
            pend = await s.pending()
            run.step("receive", total=sum(int(v) for v in pend["in"].values()), unit="changes")
            for db in dbs:
                out[db]["in"] = await s.pull_all(db)
            peer_vv = h.get("vv") or {}
            run.step("send", total=await asyncio.to_thread(_local_pending, peer_vv), unit="changes")
            for db in dbs:
                out[db]["out"] = await s.push_all(db, peer_vv.get(db, {}))
            # Files, only while the user is not active unless they asked (D15:
            # eager, but at the lowest priority) — a skipped round is picked up next.
            run.step("files", unit="files")
            if user_initiated or _user_idle():
                out["blobs"] = {**await s.fetch_blobs(), **await s.send_blobs()}
            else:
                out["blobs"] = {"deferred": "you are using LocalBook"}
        # This Mac's own index for what arrived — inside the run, so it is visible.
        out["index"] = await indexer.run_into(run, paced=not user_initiated)
        store.update_device(device_id, last_seen=time.time(), last_error=None)
        store.put(f"last_sync:{device_id}", {"at": time.time(), "result": out})
        return out
    except progress.Cancelled:
        store.update_device(device_id, last_error=None)
        raise
    except peer.VersionSkew as exc:
        store.update_device(device_id, last_error=str(exc))
        raise
    except Exception as exc:
        store.update_device(device_id, last_error=f"{type(exc).__name__}: {exc}"[:300])
        raise
    finally:
        _busy.pop(device_id, None)


def _local_pending(peer_vv: Dict[str, Dict[str, int]]) -> int:
    return sum(peer._pending_all(peer_vv).values())


# ── runs: what Start sync / Sync now start, in the background ────────────────


FIRST_PHASES = ["backup", "connect", "match", "receive", "send", "files", "index"]
SYNC_PHASES = ["connect", "receive", "send", "files", "index"]


def start_run(device_id: str, first: bool = False) -> Dict[str, Any]:
    """Start a sync in the background and return at once; the screen follows it
    through /sync/progress. (Holding the request open for minutes is how the
    WebKit network process gives up — CLAUDE.md.)"""
    if progress.active("initiated"):
        raise ValueError("a sync is already running — stop it first or wait for it")
    d = _device(device_id)
    phases = list(FIRST_PHASES) if first else list(SYNC_PHASES)
    if first and store.get("first_apply_backup"):
        phases.remove("backup")
    if first and (d.get("role") != "seed" or store.get(f"genesis_done:{device_id}")):
        phases.remove("match")
    run = progress.begin("initiated", "initiated", phases, device_id=device_id, name=d.get("name"))

    async def _go():
        try:
            res = await (apply(device_id, run) if first else sync_with(device_id, True, run))
            run.finish(result=summarize(res))
        except progress.Cancelled:
            run.finish(result={"stopped": True})
        except Exception as exc:
            logger.warning("[sync] run with %s failed: %s", d.get("name") or device_id, exc)
            run.finish(error=f"{type(exc).__name__}: {exc}"[:300] if not isinstance(exc, ValueError)
                       else str(exc))

    from utils.tasks import safe_create_task
    safe_create_task(_go(), name=f"sync-run-{run.run_id}")
    return {"run_id": run.run_id}


def summarize(out: Dict[str, Any]) -> Dict[str, Any]:
    """What a run moved, per table, for the plain-words summary on screen."""
    got: Dict[str, int] = {}
    sent: Dict[str, int] = {}
    conflicts = 0
    for db, io in out.items():
        if not isinstance(io, dict) or "in" not in io:
            continue
        for side, acc in (("in", got), ("out", sent)):
            rep = io.get(side) or {}
            conflicts += int(rep.get("conflicts", 0)) if side == "in" else 0
            for t, st in (rep.get("tables") or {}).items():
                if st.get("changed"):
                    acc[t] = acc.get(t, 0) + int(st["changed"])
    return {"received": got, "sent": sent, "conflicts": conflicts,
            "files": out.get("blobs") or {}, "index": out.get("index") or {}}


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
        if progress.active("initiated"):
            break                                      # the user's own run is going
        run = progress.begin("initiated", "initiated", list(SYNC_PHASES), device_id=d["device_id"],
                             name=d.get("name"), quiet=True)
        try:
            results[d["device_id"]] = await sync_with(d["device_id"], run=run)
            run.finish(result=summarize(results[d["device_id"]]))
        except progress.Cancelled:
            run.finish(result={"stopped": True})
        except Exception as exc:
            run.finish(error=str(exc)[:300])
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
            if store.enabled() and not store.devices() \
                    and float(store.get("pairing_open_until", 0)) <= time.time():
                # No partner yet: stay findable (the window is only for pairing,
                # and pairing still needs the code confirmed on both Macs).
                await peer.open_pairing_window()
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
                          "model_mismatch": store.get(f"model_mismatch:{d['device_id']}") or {},
                          "preview_state": store.get(f"preview_state:{d['device_id']}")})
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
        "collector": _collector_status(),
    }


def _collector_status() -> Dict[str, Any]:
    from services.sync import roles
    here, why = roles.collects_here()
    return {"chosen": roles.collector(), "here": here, "reason": why}
