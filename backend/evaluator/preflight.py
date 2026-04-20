"""Evaluator preflight checks.

Fail fast and explain clearly before a run starts. For the split
Ollama/llama-server setup, the most common time sink is discovering 30 minutes
into a run that the sidecar wasn't actually healthy, or that the registered
Bonsai model file had been moved. Preflight catches those cases up front.

Checks performed
----------------
1. RAM headroom (≥ 1 GB free).
2. Active main-model backend reachable:
     - Ollama path → GET /api/version
     - llama-server path → GET /health (sidecar)
3. Active fast-model backend reachable (may be same as main).
4. Embedding backend reachable (always Ollama today).
5. Vision backend reachable when a vision model is configured.
6. Model file exists for sidecar models (Bonsai GGUF path).

Each check returns a `PreflightCheck` with status in
{"pass", "warn", "fail"} so the evaluator service can decide whether to
abort, skip a category, or just surface a banner warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import os
import logging

from services.llm_provider import (
    resolve as _resolve_provider,
    Provider as _Provider,
    health_check as _provider_health,
)

logger = logging.getLogger(__name__)


# ─── Result shapes ─────────────────────────────────────────────────────────

@dataclass
class PreflightCheck:
    name: str
    status: str             # "pass" | "warn" | "fail"
    message: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PreflightReport:
    checks: list = field(default_factory=list)   # list[PreflightCheck]

    @property
    def blocking_failure(self) -> Optional[str]:
        for c in self.checks:
            if c.status == "fail":
                return c.message
        return None

    def to_dict(self) -> dict:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "blocking_failure": self.blocking_failure,
        }


# ─── Individual checks ─────────────────────────────────────────────────────

def _check_memory() -> PreflightCheck:
    try:
        import psutil
        mem = psutil.virtual_memory()
        available_gb = mem.available / (1024 ** 3)
        total_gb = mem.total / (1024 ** 3)
        if available_gb < 1.0:
            return PreflightCheck(
                name="memory",
                status="fail",
                message=(
                    f"Only {available_gb:.1f} GB free of {total_gb:.0f} GB — need ≥ 1 GB. "
                    "Close other apps or wait for current Ollama operations to finish."
                ),
                details={"available_gb": available_gb, "total_gb": total_gb},
            )
        return PreflightCheck(
            name="memory",
            status="pass",
            message=f"{available_gb:.1f} GB available",
            details={"available_gb": available_gb, "total_gb": total_gb},
        )
    except ImportError:
        return PreflightCheck(name="memory", status="warn", message="psutil not installed")


async def _check_model_backend(role: str, model_name: str) -> PreflightCheck:
    """Probe the backend that will serve `model_name` for this role.

    Returns pass/warn/fail plus the resolved provider+url for UI display.
    """
    if not model_name:
        return PreflightCheck(
            name=f"{role}_backend",
            status="warn",
            message=f"No {role} configured",
        )
    route = _resolve_provider(model_name)
    provider_str = route.provider.value
    healthy = await _provider_health(route.provider)
    base_details = {
        "role": role,
        "model": model_name,
        "provider": provider_str,
        "backend_url": route.base_url,
    }
    if healthy:
        return PreflightCheck(
            name=f"{role}_backend",
            status="pass",
            message=f"{provider_str} @ {route.base_url} healthy for {model_name}",
            details=base_details,
        )
    # Sidecar-specific hint
    if route.provider is _Provider.LLAMA_SERVER:
        return PreflightCheck(
            name=f"{role}_backend",
            status="fail",
            message=(
                f"llama-server sidecar at {route.base_url} not responding. "
                f"Open the Locker tab and start the sidecar, then retry."
            ),
            details=base_details,
        )
    return PreflightCheck(
        name=f"{role}_backend",
        status="fail",
        message=(
            f"Ollama at {route.base_url} not responding. "
            "Run `ollama serve` or check the Health Portal."
        ),
        details=base_details,
    )


async def _warm_main_model(model_name: str) -> PreflightCheck:
    """Fire a tiny throwaway generation so the first real test isn't penalised
    by cold-start time. Bounded by a 25s ceiling so a stuck backend cannot
    block the evaluator forever."""
    import time as _time
    if not model_name:
        return PreflightCheck(name="warmup", status="warn", message="No main model configured")
    try:
        import asyncio as _asyncio
        from services.ollama_client import ollama_client
        t0 = _time.time()
        resp = await _asyncio.wait_for(
            ollama_client.generate(
                prompt="ping",
                model=model_name,
                temperature=0.0,
                num_predict=4,
                timeout=25.0,
            ),
            timeout=25.0,
        )
        elapsed = _time.time() - t0
        text = (resp or {}).get("response", "") if isinstance(resp, dict) else ""
        if text.startswith("Error:") or text == "Request timed out":
            return PreflightCheck(
                name="warmup",
                status="warn",
                message=f"Warmup did not return text after {elapsed:.1f}s — first real test may be slow",
                details={"model": model_name, "elapsed_seconds": round(elapsed, 2)},
            )
        return PreflightCheck(
            name="warmup",
            status="pass",
            message=f"{model_name} warmed in {elapsed:.1f}s",
            details={"model": model_name, "elapsed_seconds": round(elapsed, 2)},
        )
    except Exception as e:
        return PreflightCheck(
            name="warmup",
            status="warn",
            message=f"Warmup failed (non-fatal): {e!s}",
            details={"model": model_name},
        )


def _check_sidecar_model_file(model_name: str) -> Optional[PreflightCheck]:
    """If the main model is served by the sidecar, verify the GGUF file exists."""
    if not model_name:
        return None
    route = _resolve_provider(model_name)
    if route.provider is not _Provider.LLAMA_SERVER:
        return None
    try:
        from services.sidecar_manager import resolve_config
        cfg = resolve_config()
        exists = os.path.exists(cfg.model_path)
        return PreflightCheck(
            name="sidecar_model_file",
            status="pass" if exists else "fail",
            message=(
                f"Model file present: {cfg.model_path}"
                if exists
                else f"Model file missing: {cfg.model_path}"
            ),
            details={"model_path": cfg.model_path, "exists": exists},
        )
    except Exception as e:
        return PreflightCheck(
            name="sidecar_model_file",
            status="warn",
            message=f"Could not inspect sidecar config: {e}",
        )


# ─── Orchestration ─────────────────────────────────────────────────────────

async def run_preflight(settings_obj) -> PreflightReport:
    """Run every preflight check. Never raises — reports failures inline.

    `settings_obj` is the live `config.settings` module/object so preflight
    sees the current Locker state, not an import-time snapshot.
    """
    report = PreflightReport()

    report.checks.append(_check_memory())

    main_model = getattr(settings_obj, "ollama_model", "") or ""
    fast_model = getattr(settings_obj, "ollama_fast_model", "") or ""
    embedding_model = getattr(settings_obj, "embedding_model", "") or ""
    vision_model = getattr(settings_obj, "vision_model", "") or ""

    report.checks.append(await _check_model_backend("main", main_model))
    if fast_model and fast_model != main_model:
        report.checks.append(await _check_model_backend("fast", fast_model))
    if embedding_model:
        report.checks.append(await _check_model_backend("embedding", embedding_model))
    if vision_model and vision_model != main_model:
        report.checks.append(await _check_model_backend("vision", vision_model))

    sidecar_file_check = _check_sidecar_model_file(main_model)
    if sidecar_file_check is not None:
        report.checks.append(sidecar_file_check)

    # Warm the main model only if its backend passed — skips the expense
    # when preflight is already going to abort anyway.
    main_ok = any(
        c.name == "main_backend" and c.status == "pass" for c in report.checks
    )
    if main_ok and main_model:
        report.checks.append(await _warm_main_model(main_model))

    return report


# ─── Provider provenance summary ───────────────────────────────────────────

def providers_used_summary(settings_obj) -> dict:
    """Build the {role: {provider, backend_url, model}} map for the summary."""
    out: dict = {}
    for role_attr, role_key in (
        ("ollama_model", "main"),
        ("ollama_fast_model", "fast"),
        ("embedding_model", "embedding"),
        ("vision_model", "vision"),
    ):
        model_name = getattr(settings_obj, role_attr, "") or ""
        if not model_name:
            continue
        route = _resolve_provider(model_name)
        out[role_key] = {
            "model": model_name,
            "provider": route.provider.value,
            "backend_url": route.base_url,
        }
    return out


# ─── Smoke tests ───────────────────────────────────────────────────────────

def _run_smoke_tests():
    class _FakeSettings:
        ollama_model = "bonsai-8b"
        ollama_fast_model = "phi4-mini:latest"
        embedding_model = "embeddinggemma"
        vision_model = ""

    import asyncio
    rep = asyncio.run(run_preflight(_FakeSettings()))
    assert isinstance(rep, PreflightReport)
    # Must have at least memory + main_backend + fast_backend + embedding_backend
    names = {c.name for c in rep.checks}
    assert "memory" in names
    assert "main_backend" in names
    # providers_used_summary works without raising
    summary = providers_used_summary(_FakeSettings())
    assert summary["main"]["provider"] == "llama_server"
    assert summary["fast"]["provider"] == "ollama"
    print("[evaluator.preflight] Smoke tests passed.")


if __name__ == "__main__":
    _run_smoke_tests()
