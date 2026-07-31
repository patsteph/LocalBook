"""GitHub incident emitter — the client→tracker seam for Quality-Signals Phase 2 (slice 2b).

Slice 2a queued SCRUBBED incidents to `signals/incident_queue.jsonl` as
`{"queued_at", "incident"}` lines (see `quality_signals.escalate_to_incident`). This module
is the ONLY place that would turn one of those queued incidents into a GitHub issue — and it
is built to be safe by construction:

  - **Default-OFF.** Sending is gated on an explicit enabled flag (a local config file, or a
    `settings.incidents_enabled` override). Missing/unset ⇒ disabled. `file_incidents()` refuses
    unless the flag is on AND the caller passes `confirm=True` (Decision #5 = explicit click).
  - **Preview never sends.** `preview_incidents()` reads the queue and returns exactly what WOULD
    be filed (the already-scrubbed bytes) — zero network, zero subprocess.
  - **Verbatim.** The issue title+body are built from the ALREADY-SCRUBBED queued incident. We do
    NOT re-assemble or re-scrub — the payload the user previews is the payload that gets sent.
  - **Idempotent.** A local `signals/incident_filed.json` map (`{inc_id: {...}}`) is consulted
    before every send; an already-filed `incident_id` is skipped. `incident_id` is stable per
    `(type, component, key)`, so the same field-edge never double-files, even across restarts.
  - **Never raises.** Every public method returns a structured result dict; a broken queue line,
    an unwritable path, or a missing auth provider degrades to a safe partial result.

Auth seam (Decision #3), tried in order:
  1. `gh` CLI passthrough — `gh auth status` clean ⇒ `gh issue create …`. Zero secret handling.
  2. Keychain PAT fallback — `keychain_manager.get_api_key("github_token")` ⇒ `httpx` REST POST.
  (Device-Flow is deferred; the `AuthProvider` interface leaves room for it.)

All the network/subprocess touchpoints are isolated in tiny seam methods so tests can monkeypatch
them and prove NO real call is reachable without an explicit enabled+confirm.

See READFIRST/in-progress/quality-signals-observability.md §"2b/2c/2d build-ready spec".
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
REPO = "patsteph/LocalBook"
ISSUE_LABELS = ("incident", "quality-signal")
_GH_TIMEOUT = 20.0          # short — a hung gh/network can never stall the caller
_GITHUB_API = "https://api.github.com"


class GithubIncidentEmitter:
    """Reads the local incident queue and (only on explicit confirm+enable) files issues.

    Singleton + thread-safe. Never raises from a public method.
    """

    _instance: Optional["GithubIncidentEmitter"] = None
    _new_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._new_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._file_lock = threading.Lock()
        self._initialized = True

    # ── Paths (derive from the 2a sink so we read the exact same queue) ──────
    def _signals_dir(self) -> Path:
        """The signals dir — sourced from the 2a sink so tests that repoint the
        sink's `signals_dir` transparently repoint us too."""
        try:
            from services.quality_signals import quality_signals
            return Path(quality_signals.signals_dir)
        except Exception:
            return Path(settings.data_dir) / "signals"

    def _queue_path(self) -> Path:
        try:
            from services.quality_signals import quality_signals
            return quality_signals._incident_queue_path()
        except Exception:
            return self._signals_dir() / "incident_queue.jsonl"

    def _filed_path(self) -> Path:
        """Local idempotency store: `{inc_id: {issue, sent_at, method}}`."""
        return self._signals_dir() / "incident_filed.json"

    def _config_path(self) -> Path:
        return self._signals_dir() / "github_incident_config.json"

    # ── Default-OFF flag ─────────────────────────────────────────────────────
    def is_enabled(self) -> bool:
        """True only when sending is explicitly enabled. Defaults to False.

        A `settings.incidents_enabled` bool (if the app ever defines one) wins;
        otherwise the local config file; otherwise disabled. Never raises.
        """
        try:
            override = getattr(settings, "incidents_enabled", None)
            if isinstance(override, bool):
                return override
        except Exception:
            pass
        try:
            p = self._config_path()
            if p.is_file():
                data = json.loads(p.read_text())
                return bool(data.get("enabled", False))
        except Exception as e:
            logger.debug(f"[github-incident] enabled-flag read failed (non-fatal): {e}")
        return False

    def set_enabled(self, enabled: bool) -> bool:
        """Persist the enabled flag to the local config file. Returns the new state.

        Never raises — on a write failure it logs and returns the current state.
        """
        try:
            self._signals_dir().mkdir(parents=True, exist_ok=True)
            with self._file_lock:
                self._config_path().write_text(json.dumps({"enabled": bool(enabled)}))
            return bool(enabled)
        except Exception as e:
            logger.warning(f"[github-incident] could not persist enabled flag: {e}")
            return self.is_enabled()

    # ── Idempotency store ────────────────────────────────────────────────────
    def _load_filed(self) -> Dict[str, Any]:
        try:
            p = self._filed_path()
            if p.is_file():
                data = json.loads(p.read_text())
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.debug(f"[github-incident] filed-map read failed (non-fatal): {e}")
        return {}

    def _record_filed(self, incident_id: str, entry: Dict[str, Any]) -> None:
        """Persist that `incident_id` was filed. Never raises."""
        try:
            self._signals_dir().mkdir(parents=True, exist_ok=True)
            with self._file_lock:
                current = self._load_filed()
                current[incident_id] = entry
                self._filed_path().write_text(json.dumps(current, indent=2))
        except Exception as e:
            logger.warning(f"[github-incident] could not record filed id {incident_id}: {e}")

    # ── Queue read ───────────────────────────────────────────────────────────
    def _read_queue(self) -> List[Dict[str, Any]]:
        """Return the queued (already-scrubbed) incident dicts, in file order.

        Skips broken/unparseable lines silently. Never raises. Later duplicate
        `incident_id`s collapse to the LATEST queued copy (re-escalation refreshes
        the payload) while preserving first-seen order.
        """
        out: List[Dict[str, Any]] = []
        seen: Dict[str, int] = {}
        try:
            path = self._queue_path()
            if not path.is_file():
                return []
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        incident = rec.get("incident")
                        if not isinstance(incident, dict):
                            continue
                        inc_id = str(incident.get("incident_id", "") or "")
                        if not inc_id:
                            continue
                        if inc_id in seen:
                            out[seen[inc_id]] = incident   # refresh in place
                        else:
                            seen[inc_id] = len(out)
                            out.append(incident)
                    except Exception:
                        continue  # a single broken line must never break the read
        except Exception as e:
            logger.debug(f"[github-incident] queue read failed (non-fatal): {e}")
            return out
        return out

    # ── Title + body from the ALREADY-SCRUBBED incident (verbatim) ───────────
    def _issue_title(self, incident: Dict[str, Any]) -> str:
        inc_id = str(incident.get("incident_id", "inc_unknown"))
        title = str(incident.get("title", "") or "quality-signal incident")
        return f"[{inc_id}] {title}"

    def _issue_body(self, incident: Dict[str, Any]) -> str:
        """Render the scrubbed incident as an issue body. Content is verbatim from
        the queued (scrubbed) dict — this method only formats, never fetches or scrubs.

        The `<!-- lb-incident: inc_xxx -->` marker + the id in the title let the tracker
        be searched for an existing issue (idempotency at the GitHub layer, on top of the
        local filed-map).
        """
        inc_id = str(incident.get("incident_id", "inc_unknown"))
        sig = incident.get("signal") if isinstance(incident.get("signal"), dict) else {}
        env = incident.get("environment") if isinstance(incident.get("environment"), dict) else {}
        metrics = incident.get("metrics") if isinstance(incident.get("metrics"), dict) else {}
        promotion = incident.get("promotion") if isinstance(incident.get("promotion"), dict) else {}
        samples = incident.get("samples") if isinstance(incident.get("samples"), list) else []

        lines: List[str] = []
        lines.append(f"<!-- lb-incident: {inc_id} -->")
        lines.append("")
        lines.append("> Auto-generated from a LocalBook Quality-Signals near-miss. "
                     "All fields are allowlist-scrubbed (local-only privacy scrub).")
        lines.append("")
        lines.append("## Signal")
        lines.append(f"- **type**: {sig.get('type', 'unknown')}")
        lines.append(f"- **component**: {sig.get('component', 'unknown')}")
        if sig.get("key"):
            lines.append(f"- **key**: {sig.get('key')}")
        lines.append(f"- **severity**: {sig.get('severity', 'notable')}")
        lines.append(f"- **detail**: {sig.get('detail', '')}")
        lines.append(f"- **count**: {sig.get('count', 1)}")
        if sig.get("first_seen"):
            lines.append(f"- **first_seen**: {sig.get('first_seen')}")
        if sig.get("last_seen"):
            lines.append(f"- **last_seen**: {sig.get('last_seen')}")

        if metrics:
            lines.append("")
            lines.append("## Recurrence")
            for k in ("count", "distinct_days", "window_days"):
                if k in metrics:
                    lines.append(f"- **{k}**: {metrics.get(k)}")
        if promotion:
            lines.append("")
            lines.append("## Promotion verdict")
            lines.append(f"- **eligible**: {promotion.get('eligible')}")
            if promotion.get("reason"):
                lines.append(f"- **reason**: {promotion.get('reason')}")

        if samples:
            lines.append("")
            lines.append("## Triggering samples (scrubbed)")
            for s in samples:
                lines.append(f"- `{s}`")

        lines.append("")
        lines.append("## Environment")
        for k in ("app_version", "git_sha", "platform", "platform_release", "arch", "python_version"):
            if k in env:
                lines.append(f"- **{k}**: {env.get(k)}")

        lines.append("")
        lines.append(f"<sub>incident_id `{inc_id}` · schema v{incident.get('schema_version', 1)}</sub>")
        return "\n".join(lines)

    # ── Preview (SAFE — never sends) ─────────────────────────────────────────
    def preview_incidents(self) -> List[Dict[str, Any]]:
        """Return what WOULD be filed for each queued incident. NO network, NO send.

        Each item: `{incident_id, title, body, already_filed, filed}`.
        """
        filed = self._load_filed()
        out: List[Dict[str, Any]] = []
        for incident in self._read_queue():
            try:
                inc_id = str(incident.get("incident_id", ""))
                out.append({
                    "incident_id": inc_id,
                    "title": self._issue_title(incident),
                    "body": self._issue_body(incident),
                    "already_filed": inc_id in filed,
                    "filed": filed.get(inc_id),
                })
            except Exception as e:
                logger.debug(f"[github-incident] preview render failed (non-fatal): {e}")
                continue
        return out

    # ── Auth seams (isolated so tests can monkeypatch — NO real call ever) ───
    def _gh_auth_ok(self) -> bool:
        """True when the `gh` CLI is installed AND authenticated. Never raises."""
        try:
            proc = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True, text=True, timeout=_GH_TIMEOUT,
            )
            return proc.returncode == 0
        except Exception as e:
            logger.debug(f"[github-incident] gh auth status unavailable: {e}")
            return False

    def _gh_create_issue(self, title: str, body: str) -> Dict[str, Any]:
        """Create an issue via the `gh` CLI. Returns `{ok, issue?, error?}`. Never raises."""
        try:
            args = [
                "gh", "issue", "create",
                "--repo", REPO,
                "--title", title,
                "--body", body,
            ]
            for label in ISSUE_LABELS:
                args += ["--label", label]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=_GH_TIMEOUT)
            if proc.returncode == 0:
                # gh prints the issue URL on success.
                return {"ok": True, "issue": (proc.stdout or "").strip()}
            return {"ok": False, "error": (proc.stderr or "gh issue create failed").strip()}
        except Exception as e:
            return {"ok": False, "error": f"gh create raised: {e}"}

    def _pat_token(self) -> Optional[str]:
        """Read the GitHub PAT from Keychain. Never raises."""
        try:
            from services.keychain_manager import get_api_key
            return get_api_key("github_token")
        except Exception as e:
            logger.debug(f"[github-incident] keychain PAT read failed: {e}")
            return None

    def _pat_create_issue(self, token: str, title: str, body: str) -> Dict[str, Any]:
        """Create an issue via the REST API + a PAT. Returns `{ok, issue?, error?}`. Never raises."""
        try:
            import httpx
            resp = httpx.post(
                f"{_GITHUB_API}/repos/{REPO}/issues",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"title": title, "body": body, "labels": list(ISSUE_LABELS)},
                timeout=_GH_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {"ok": True, "issue": data.get("html_url") or str(data.get("number", ""))}
            return {"ok": False, "error": f"github REST {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"ok": False, "error": f"REST create raised: {e}"}

    def auth_status(self) -> Dict[str, Any]:
        """Which provider WOULD be used, without sending. Never raises."""
        if self._gh_auth_ok():
            return {"provider": "gh", "available": True}
        if self._pat_token():
            return {"provider": "pat", "available": True}
        return {"provider": None, "available": False}

    def _send_one(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        """File ONE incident via the auth seam (gh first, then PAT). Never raises."""
        title = self._issue_title(incident)
        body = self._issue_body(incident)
        # 1) gh CLI passthrough
        if self._gh_auth_ok():
            res = self._gh_create_issue(title, body)
            if res.get("ok"):
                return {"ok": True, "issue": res.get("issue"), "method": "gh"}
            gh_err = res.get("error")
        else:
            gh_err = None
        # 2) Keychain PAT fallback
        token = self._pat_token()
        if token:
            res = self._pat_create_issue(token, title, body)
            if res.get("ok"):
                return {"ok": True, "issue": res.get("issue"), "method": "pat"}
            return {"ok": False, "error": res.get("error"), "method": "pat"}
        return {"ok": False, "error": gh_err or "no auth provider (gh not authed, no Keychain PAT)",
                "method": None}

    # ── File (the ONLY send path — gated OFF + confirm) ──────────────────────
    def file_incidents(self, confirm: bool = False) -> Dict[str, Any]:
        """File every not-yet-filed queued incident — but ONLY when enabled AND confirmed.

        Returns a structured result; NEVER raises. When disabled or unconfirmed it
        short-circuits BEFORE any auth/network touch (`refused` is set, `sent` empty).

        Result:
          {ok, enabled, confirmed, refused, sent[], skipped[], failed[]}
        """
        enabled = self.is_enabled()
        result: Dict[str, Any] = {
            "ok": False,
            "enabled": enabled,
            "confirmed": bool(confirm),
            "refused": None,
            "sent": [],
            "skipped": [],
            "failed": [],
        }
        if not enabled:
            result["refused"] = "incidents are disabled (default-OFF); enable before filing"
            return result
        if not confirm:
            result["refused"] = "explicit confirm=True required to file"
            return result

        try:
            filed = self._load_filed()
            for incident in self._read_queue():
                inc_id = str(incident.get("incident_id", ""))
                if not inc_id:
                    continue
                if inc_id in filed:
                    result["skipped"].append({"incident_id": inc_id, "reason": "already_filed"})
                    continue
                res = self._send_one(incident)
                if res.get("ok"):
                    entry = {
                        "issue": res.get("issue"),
                        "method": res.get("method"),
                        "sent_at": datetime.utcnow().isoformat(),
                    }
                    self._record_filed(inc_id, entry)
                    filed[inc_id] = entry
                    result["sent"].append({"incident_id": inc_id, **entry})
                else:
                    result["failed"].append({"incident_id": inc_id, "error": res.get("error")})
            result["ok"] = len(result["failed"]) == 0
        except Exception as e:
            logger.warning(f"[github-incident] file_incidents failed (non-fatal): {e}")
            result["refused"] = f"unexpected error: {e}"
        return result


# Singleton + module-level convenience wrappers (mirror the other service seams).
github_incident = GithubIncidentEmitter()


def preview_incidents() -> List[Dict[str, Any]]:
    """Convenience wrapper — see GithubIncidentEmitter.preview_incidents. Never sends."""
    return github_incident.preview_incidents()


def file_incidents(confirm: bool = False) -> Dict[str, Any]:
    """Convenience wrapper — see GithubIncidentEmitter.file_incidents. Never raises."""
    return github_incident.file_incidents(confirm=confirm)
