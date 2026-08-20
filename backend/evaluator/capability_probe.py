"""Engine-agnostic, PROBE-FIRST model capability detection (Locker rebuild — build A).

Replaces the registry-first guessing that defaulted every uncurated model to
text-only / no-vision / no-embeddings / no-roles — the cause of "Qwen vision model
told to install granite" and "5 fresh models all slotted into Main." Ground truth
is now the ENGINE itself:

  • config.json → model_type / architectures / vision_config / quantization /
                  max_position_embeddings, read from the local snapshot.

The Ollama /api/show probe that used to sit alongside this went with the transport.

The static registry (known_models.json) is DEMOTED to OVERRIDES/enrichment only
(license, origin, policy tags, curated display names, manual capability pins) —
never the gate. Capability-based ROLES fall straight out of the probed flags, so
slotting stops being size-based.

Cheap + cached; safe to call from the registry card builder, the Locker swap
analysis, and the evaluator's capability gate.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

@dataclass
class ProbedCapabilities:
    """Resolved, engine-reported capabilities for one model. JSON-serialisable."""
    model: str = ""
    provider: str = "mlx"
    source: str = "probe"            # probe | probe+registry | registry | fallback
    text: bool = True
    vision: bool = False
    embedding: bool = False
    audio: bool = False
    tools: bool = False
    thinking: bool = False
    native_ctx: int = 0
    embedding_dim: int = 0
    param_size: str = ""             # "8.0B" / "566.70M"
    param_count_b: float = 0.0       # billions of params
    quantization: str = ""           # "Q4_K_M" / "F16"
    family: str = ""
    raw_capabilities: list = field(default_factory=list)

    def roles(self) -> list[str]:
        """Capability-based role eligibility — THE slotting fix.

        A model is eligible for a slot iff it has the matching capability. A
        vision/embedding model no longer masquerades as Main; a text model no
        longer needs a curated entry to be assignable.
        """
        r: list[str] = []
        if self.embedding:
            r.append("embedding_model")
        if self.text and not self.embedding:
            # Pure-embedding models are not text generators; everything else that
            # can complete is eligible for both text slots (size decides which is
            # the sensible default, but the user may pin either).
            r.append("main_model")
            r.append("fast_model")
        if self.vision:
            r.append("vision_model")
        return r

    def to_dict(self) -> dict:
        d = asdict(self)
        d["roles"] = self.roles()
        return d


# ── Probe interface (engine-agnostic) ───────────────────────────────────────────
@runtime_checkable
class CapabilityProbe(Protocol):
    provider: str
    def probe(self, model: str) -> Optional[ProbedCapabilities]: ...


# ── Small TTL cache (per model) ──────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, ProbedCapabilities]] = {}
_TTL = 300.0


def _cache_get(key: str) -> Optional[ProbedCapabilities]:
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1]
    return None


def _cache_put(key: str, caps: ProbedCapabilities) -> None:
    _CACHE[key] = (time.time(), caps)


def invalidate_cache(model: Optional[str] = None) -> None:
    if model is None:
        _CACHE.clear()
    else:
        _CACHE.pop(f"mlx::{model}", None)


def _parse_param_count_b(param_size: str) -> float:
    # Local import keeps ram_fit the single source of truth for the parser.
    from evaluator.ram_fit import parse_param_count_b
    return parse_param_count_b(param_size)


def _estimate_param_b_from_id(model: str, cfg: dict) -> float:
    """Best-effort param count (billions) for RAM-fit. config.json has no direct count,
    so parse the model id (…-4B, e4b, mini) with a hidden-size fallback."""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", model.lower())
    if m:
        try:
            return float(m.group(1))
        except Exception:
            pass
    low = model.lower()
    if "e4b" in low or "mini" in low:
        return 4.0
    if "e2b" in low:
        return 2.0
    return 0.0


class MLXCapabilityProbe:
    """Reads the MLX checkpoint's config.json to derive capabilities.
    vision ← `vision_config`; embedding ← model_type/architectures; native_ctx ←
    max_position_embeddings; quantization ← `quantization.bits`. Only a cheap
    config.json fetch (never the whole model)."""
    provider = "mlx"

    def probe(self, model: str) -> Optional[ProbedCapabilities]:
        # Reading + parsing config.json on every Locker render is pure waste, and the
        # Locker renders often.
        if not model:
            return None
        key = f"mlx::{model}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        caps = self._probe_uncached(model)
        if caps is not None:
            _cache_put(key, caps)
        return caps

    def _probe_uncached(self, model: str) -> Optional[ProbedCapabilities]:
        if not model:
            return None
        # READ THE CACHE, never the Hub. `hf_hub_download(model, "config.json")` contacts
        # huggingface.co to revalidate even when the file is already local — it emitted an
        # "unauthenticated requests to the HF Hub" warning on every Locker open, made the
        # model list depend on connectivity, and sent a request off the machine for an app
        # whose whole premise is that nothing does. `load_config` reads the cached snapshot.
        try:
            from services.model_sizing import load_config
            cfg = load_config(model)
            if not cfg:
                return None
        except Exception:
            return None
        tcfg = cfg.get("text_config", cfg) if isinstance(cfg.get("text_config"), dict) else cfg
        model_type = str(cfg.get("model_type", "") or "")
        archs = [str(a) for a in (cfg.get("architectures") or [])]
        vision = cfg.get("vision_config") is not None
        is_embed = ("embed" in model_type.lower()
                    or any("embed" in a.lower() or "roberta" in a.lower() for a in archs))
        quant = cfg.get("quantization")
        bits = quant.get("bits") if isinstance(quant, dict) else None
        ctx = int(tcfg.get("max_position_embeddings") or cfg.get("max_position_embeddings") or 0)
        hidden = int(tcfg.get("hidden_size") or cfg.get("hidden_size") or 0)
        return ProbedCapabilities(
            model=model, provider="mlx", source="probe",
            text=not is_embed, vision=vision, embedding=is_embed,
            thinking=False,  # rag_profile controls thinking suppression at call time
            native_ctx=ctx,
            embedding_dim=hidden if is_embed else 0,
            quantization=(f"Q{bits}" if bits else ""),
            param_count_b=_estimate_param_b_from_id(model, cfg),
            family=model_type,
            raw_capabilities=(["vision"] if vision else []) + (["embedding"] if is_embed else ["completion"]),
        )


# ── Dispatcher: probe-first, registry as OVERRIDE only ───────────────────────────


def probe_capabilities(model: str, provider: str = "mlx") -> Optional[ProbedCapabilities]:
    """Resolve capabilities for a model, CHECKPOINT-FIRST.

    Read the model's own config.json → if it answers, that IS the truth. The registry is
    consulted only to ADD signal the checkpoint lacks or to apply an explicit manual pin,
    never to gate or to flip a reported capability off.

    `provider` is vestigial: the Ollama /api/show probe went with the transport, so every
    model is probed the same way. Kept in the signature because ~6 call sites pass it.
    """
    if not model:
        return None
    caps: Optional[ProbedCapabilities] = MLXCapabilityProbe().probe(model)

    # Registry overlay: only ADD signal the probe lacked, or apply an explicit
    # manual pin. Never downgrade a capability the engine reported.
    try:
        from evaluator.model_registry import model_registry
        info = model_registry.get_model(model)
    except Exception:
        info = None

    if caps is None and info is not None:
        # Engine unreachable but we have a curated entry — use it, flagged.
        return ProbedCapabilities(
            model=model,
            provider=getattr(info, "provider", "ollama"),
            source="registry",
            text=True,
            vision=bool(getattr(info, "supports_vision", False)),
            embedding=bool(getattr(info, "embedding_dim", 0)),
            tools=False,
            native_ctx=int(getattr(info, "context_window", 0) or 0),
            embedding_dim=int(getattr(info, "embedding_dim", 0) or 0),
            param_size=str(getattr(info, "parameter_count", "") or ""),
            param_count_b=_parse_param_count_b(str(getattr(info, "parameter_count", "") or "")),
            family=str(getattr(info, "family", "") or ""),
        )

    if caps is not None and info is not None:
        # Curated entry present → additive overlay only.
        if not caps.vision and getattr(info, "supports_vision", False):
            caps.vision = True
        if not caps.embedding and getattr(info, "embedding_dim", 0):
            caps.embedding = True
            caps.embedding_dim = caps.embedding_dim or int(info.embedding_dim)
        if not caps.native_ctx and getattr(info, "context_window", 0):
            caps.native_ctx = int(info.context_window)
        caps.source = "probe+registry"

    return caps
