"""Evaluator capability matrix.

Single source of truth for which tests a given model can reasonably run. Consulted by test
runners and the preflight checker so the evaluator never penalises a model for a capability
it was never going to have (e.g. running vision tests on a text-only model).

Design principles
-----------------
1. Capabilities are inferred first from the static registry entry (`ModelInfo`), then
   refined by PROBING the engine — the probe only turns capabilities ON.
2. Unknown capabilities default to the most *permissive* interpretation, so community
   models aren't blocked from tests they might well pass.
3. The module is side-effect free and import-cheap so it can be called from
   inside every test runner without adding startup latency.

Public API
----------
- `capabilities_for(model_name)` → `ModelCapabilities`
- `ModelCapabilities.supports(feature)` → bool
- `ModelCapabilities.skip_reason(feature)` → str | None
- `FEATURES` — canonical feature keys used by test runners
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional



# ─── Canonical feature keys ────────────────────────────────────────────────

class FEATURES:
    """Canonical capability keys. Test runners should reference these."""
    TEXT_GENERATE = "text.generate"            # /generate, /chat, /v1/chat/completions
    TEXT_STREAM = "text.stream"                # streaming SSE from either backend
    JSON_MODE = "text.json_mode"               # format:"json" or response_format:json_object
    VISION = "vision"                          # multimodal image inputs
    EMBEDDINGS = "embeddings"                  # /api/embeddings (Ollama only today)
    LARGE_CONTEXT = "context.large"            # ≥ 16k tokens available
    CONCURRENT_REQUESTS = "concurrency"        # multiple in-flight requests
    KEEP_ALIVE = "keep_alive"                  # Ollama-style idle unload


# ─── Capabilities data shape ───────────────────────────────────────────────

@dataclass
class ModelCapabilities:
    """Resolved capability profile for a single model.

    Keep the shape flat and JSON-serialisable so it can be embedded in
    evaluator results without extra plumbing.
    """
    model: str = ""
    provider: str = "mlx"                  # historical runs may hold "ollama"/"llama_server"
    backend_url: str = ""
    context_window: int = 4096             # DEPLOYED window on THIS hardware (RAM-scaled) — the truth for eval
    native_context_window: int = 4096      # model's native ceiling (may be far larger than deployed)
    supports_vision: bool = False
    supports_embeddings: bool = False
    supports_json_mode: bool = True        # Ollama default; llama-server json_object
    supports_streaming: bool = True
    supports_concurrent_requests: bool = True
    supports_keep_alive: bool = True       # Ollama only
    # Per-feature skip reasons surfaced to test runners when a capability is False.
    skip_reasons: dict = field(default_factory=dict)

    # ── Introspection ──────────────────────────────────────────────────────
    def supports(self, feature: str) -> bool:
        mapping = {
            FEATURES.TEXT_GENERATE: True,
            FEATURES.TEXT_STREAM: self.supports_streaming,
            FEATURES.JSON_MODE: self.supports_json_mode,
            FEATURES.VISION: self.supports_vision,
            FEATURES.EMBEDDINGS: self.supports_embeddings,
            FEATURES.LARGE_CONTEXT: self.context_window >= 16384,
            FEATURES.CONCURRENT_REQUESTS: self.supports_concurrent_requests,
            FEATURES.KEEP_ALIVE: self.supports_keep_alive,
        }
        return bool(mapping.get(feature, False))

    def skip_reason(self, feature: str) -> Optional[str]:
        """Return a human-readable skip reason if feature is unsupported.

        Explicit reasons in `self.skip_reasons` take precedence over the
        generic fallbacks so runners get accurate, user-friendly text.
        """
        if self.supports(feature):
            return None
        if feature in self.skip_reasons:
            return self.skip_reasons[feature]
        # Generic fallbacks
        generic = {
            FEATURES.VISION: f"{self.model} is text-only",
            FEATURES.EMBEDDINGS: f"{self.model} is not an embedding model",
            FEATURES.JSON_MODE: f"{self.model} does not support structured JSON mode",
            FEATURES.LARGE_CONTEXT: f"{self.model} context window is {self.context_window} tokens (< 16k)",
            FEATURES.TEXT_STREAM: f"{self.model} backend does not support streaming",
        }
        return generic.get(feature, f"{self.model} does not support {feature}")

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Resolution ────────────────────────────────────────────────────────────

def _registry_entry(model_name: str):
    """Fetch the ModelInfo for this model or None."""
    if not model_name:
        return None
    try:
        from evaluator.model_registry import model_registry
        return model_registry.get_model(model_name)
    except Exception as _e:
        # Surface lookup failures so we don't silently fall back to
        # "text-only / no-embeddings / 4096 ctx" defaults for every model.
        import logging
        logging.getLogger(__name__).warning(
            f"[capabilities] registry lookup failed for '{model_name}': {_e}"
        )
        return None


def capabilities_for(model_name: str) -> ModelCapabilities:
    """Resolve the capability profile for `model_name`.

    Deterministic and cheap. Safe to call on every test iteration.
    """
    info = _registry_entry(model_name)
    # The provider axis collapsed with the sidecar, but this is NOT unconditionally "mlx":
    # a role can still point at a non-MLX model (image does, until its MLX checkpoint ships),
    # and stamping that "mlx" would mislabel the run. Prefer the registry's explicit value;
    # fall back to the id shape. NB the shape test needs both halves — `sam860/lfm2.5:350m`
    # and `x/flux2-klein:4b` are namespaced OLLAMA models, so a bare "/" check calls them MLX.
    provider_str = getattr(info, "provider", "") if info else ""
    if not provider_str:
        _n = model_name or ""
        provider_str = "mlx" if ("/" in _n and ":" not in _n) else "ollama"

    # Registry-informed defaults (Ollama path stays fully permissive).
    # Report the REAL DEPLOYED window on THIS hardware — the RAM-scaled
    # effective_num_ctx_cap the app actually gives the model at runtime — NOT the
    # model's native ceiling. This keeps the evaluator's "soft testing" aligned with
    # reality (no artificial over-statement of context capability on small boxes, and
    # bigger Macs correctly show more). Native ceiling is kept separately for context.
    native_ctx = getattr(info, "context_window", 4096) if info else 4096
    ctx = native_ctx
    # Everything runs through llm_runtime, which caps num_ctx at the RAM-scaled
    # effective_num_ctx_cap — so THAT is the true deployed window.
    try:
        from services.llm_runtime import effective_num_ctx_cap
        _eff = effective_num_ctx_cap(model_name)
        if _eff:
            ctx = _eff
    except Exception:
        pass
    supports_vision = getattr(info, "supports_vision", False) if info else False
    supports_embeddings = bool(getattr(info, "embedding_dim", 0)) if info else False
    supports_json = getattr(info, "supports_json_mode", True) if info else True

    # Build A (2026-07-07): PROBE-FIRST. Ask the engine for real capabilities so
    # the evaluator gates uncurated models on truth, not registry text-only
    # defaults. The probe only turns capabilities ON (never off a registry-declared
    # one) and supplies a real native ctx when the registry lacked one.
    try:
        from evaluator.capability_probe import probe_capabilities
        probed = probe_capabilities(model_name)
        if probed is not None:
            supports_vision = supports_vision or probed.vision
            supports_embeddings = supports_embeddings or probed.embedding
            if (not native_ctx or native_ctx == 4096) and probed.native_ctx:
                native_ctx = probed.native_ctx
                if ctx == 4096:
                    ctx = probed.native_ctx
    except Exception:
        pass

    skip_reasons: dict = {}
    # In-process: no daemon TTL to keep a model alive against, so this is vacuously true.
    supports_keep_alive = True

    return ModelCapabilities(
        model=model_name or "",
        provider=provider_str,
        backend_url="in-process" if provider_str == "mlx" else "",
        context_window=int(ctx),
        native_context_window=int(native_ctx),
        supports_vision=bool(supports_vision),
        supports_embeddings=bool(supports_embeddings),
        supports_json_mode=bool(supports_json),
        supports_streaming=True,
        supports_concurrent_requests=True,
        supports_keep_alive=supports_keep_alive,
        skip_reasons=skip_reasons,
    )


# ─── Smoke tests ───────────────────────────────────────────────────────────

def _run_smoke_tests():
    """Minimal invariants — run with `python -m evaluator.capabilities`."""
    # A curated text model (larger context, no vision, no embeddings)
    olmo = capabilities_for("olmo-3:7b-instruct")
    assert olmo.supports(FEATURES.TEXT_GENERATE)
    assert not olmo.supports(FEATURES.VISION)

    # Unknown model — permissive defaults so community models aren't blocked
    unknown = capabilities_for("totally-made-up-model:1b")
    assert unknown.supports(FEATURES.TEXT_GENERATE)
    assert unknown.supports(FEATURES.JSON_MODE)

    # Empty model name — still safe (no crash)
    empty = capabilities_for("")
    assert empty.provider

    print("[evaluator.capabilities] All smoke tests passed.")


if __name__ == "__main__":
    _run_smoke_tests()
