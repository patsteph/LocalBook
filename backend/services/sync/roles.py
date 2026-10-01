"""Which Mac does what, once Macs share a library (LB-12).

Collections: the collector settings sync, but each Mac kept its own schedule, so
every Mac collected every notebook — duplicate searching, scraping and fast-model
work, and the occasional same article added twice when two Macs collected before
they synced. Now ONE Mac runs scheduled collections and sync brings the results.

The choice is a synced document (`sync_settings/collector_device`), so it is one
setting for all Macs: change it on any Mac and the others follow. Collecting on
demand ("collect now") still works on any Mac — only the schedule is gated.

Never lets collections silently stop: with sync off, nothing chosen, or the
chosen Mac no longer paired, a Mac collects for itself as before.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

KIND, KEY = "sync_settings", "collector_device"


def collector() -> Optional[Dict[str, Any]]:
    from storage import documents

    v = documents.get(KIND, KEY)
    return v if isinstance(v, dict) and v.get("device_id") else None


def set_collector(device_id: str, name: Optional[str] = None) -> Dict[str, Any]:
    from storage import documents

    body = {"device_id": device_id, "name": name or device_id}
    documents.put(KIND, KEY, body)
    return body


def collects_here() -> Tuple[bool, str]:
    """(should this Mac run scheduled collections, why) — never raises."""
    try:
        from services.sync import identity, store

        if not store.enabled():
            return True, "sync is off — this Mac collects for itself"
        chosen = collector()
        if not chosen:
            return True, "no collector chosen — every Mac collects"
        if chosen["device_id"] == identity.device_id():
            return True, "this Mac is the collector"
        if chosen["device_id"] not in {d["device_id"] for d in store.devices()}:
            return True, "the chosen collector is no longer paired — this Mac collects"
        return False, f"collections run on {chosen.get('name') or 'another Mac'}"
    except Exception as exc:                       # a broken check must not stop collecting
        return True, f"collector check failed ({type(exc).__name__}) — collecting"
