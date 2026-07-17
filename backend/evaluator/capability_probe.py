"""Engine-agnostic, PROBE-FIRST model capability detection (Locker rebuild — build A).

Replaces the registry-first guessing that defaulted every uncurated model to
text-only / no-vision / no-embeddings / no-roles — the cause of "Qwen vision model
told to install granite" and "5 fresh models all slotted into Main." Ground truth
is now the ENGINE itself:

  • Ollama  → POST /api/show → `capabilities` array (completion/vision/embedding/
              audio/tools/thinking/insert) + model_info (native ctx, embedding dim)
              + details (parameter_size, quantization_level).  [implemented here]
  • MLX     → config.json model_type/architectures/vision_config + tokenizer
              chat_template (+ chat_template.jinja).            [build D — stub below]

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
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# ── Ollama capability tokens (confirmed live on ollama 0.31, 2026-07-07) ─────────
CAP_COMPLETION = "completion"
CAP_VISION = "vision"
CAP_EMBEDDING = "embedding"
CAP_AUDIO = "audio"
CAP_TOOLS = "tools"
CAP_THINKING = "thinking"
CAP_INSERT = "insert"


@dataclass
class ProbedCapabilities:
    """Resolved, engine-reported capabilities for one model. JSON-serialisable."""
    model: str = ""
    provider: str = "ollama"
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
        _CACHE.pop(f"ollama::{model}", None)


def _parse_param_count_b(param_size: str) -> float:
    # Local import keeps ram_fit the single source of truth for the parser.
    from evaluator.ram_fit import parse_param_count_b
    return parse_param_count_b(param_size)


# ── Shared, cached raw /api/show (reused by the probe AND run_profile) ────────────
_SHOW_CACHE: dict[str, tuple[float, dict]] = {}


def ollama_show(model: str, base_url: Optional[str] = None, timeout: float = 5.0) -> Optional[dict]:
    """POST /api/show for `model`, cached (TTL). Returns the raw payload or None
    when Ollama is unreachable / the model isn't pulled."""
    if not model:
        return None
    if base_url is None:
        try:
            from config import settings
            base_url = settings.ollama_base_url
        except Exception:
            base_url = "http://localhost:11434"
    base_url = base_url.rstrip("/")
    key = f"{base_url}::{model}"
    hit = _SHOW_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1]
    try:
        req = urllib.request.Request(
            f"{base_url}/api/show",
            data=json.dumps({"name": model}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        _SHOW_CACHE[key] = (time.time(), data)
        return data
    except Exception as e:
        logger.debug(f"[capability_probe] /api/show failed for {model!r}: {e}")
        return None


# ── Ollama implementation ────────────────────────────────────────────────────────
class OllamaCapabilityProbe:
    provider = "ollama"

    def __init__(self, base_url: Optional[str] = None, timeout: float = 5.0):
        if base_url is None:
            try:
                from config import settings
                base_url = settings.ollama_base_url
            except Exception:
                base_url = "http://localhost:11434"
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _show(self, model: str) -> Optional[dict]:
        return ollama_show(model, base_url=self.base_url, timeout=self.timeout)

    @staticmethod
    def from_show(model: str, data: dict) -> ProbedCapabilities:
        """Pure parser — build ProbedCapabilities from an /api/show payload.
        Split out so tests can exercise the real shapes with no network."""
        caps = [str(c).lower() for c in (data.get("capabilities") or [])]
        details = data.get("details") or {}
        mi = data.get("model_info") or {}
        arch = str(mi.get("general.architecture") or details.get("family") or "")

        def _mi_int(suffix: str) -> int:
            for k, v in mi.items():
                if k.endswith(suffix) and isinstance(v, (int, float)):
                    return int(v)
            return 0

        has_caps = bool(caps)
        vision = CAP_VISION in caps
        embedding = CAP_EMBEDDING in caps
        text = (CAP_COMPLETION in caps) or (CAP_INSERT in caps)

        # Fallback heuristics ONLY when the runner is too old to report a
        # capabilities array (ollama ≥ ~0.5 always does; guard anyway).
        if not has_caps:
            fam = f"{arch} {model}".lower()
            embedding = ("embed" in fam) or ("bert" in fam)
            vision = any(t in fam for t in ("vision", "llava", "-vl", "vl-", "moondream", "bakllava"))
            text = not embedding

        param_size = str(details.get("parameter_size") or "")
        return ProbedCapabilities(
            model=model,
            provider="ollama",
            source="probe" if has_caps else "fallback",
            text=bool(text),
            vision=bool(vision),
            embedding=bool(embedding),
            audio=CAP_AUDIO in caps,
            tools=CAP_TOOLS in caps,
            thinking=CAP_THINKING in caps,
            native_ctx=_mi_int(".context_length"),
            embedding_dim=_mi_int(".embedding_length"),
            param_size=param_size,
            param_count_b=_parse_param_count_b(param_size),
            quantization=str(details.get("quantization_level") or ""),
            family=str(details.get("family") or arch),
            raw_capabilities=caps,
        )

    def probe(self, model: str) -> Optional[ProbedCapabilities]:
        if not model:
            return None
        key = f"ollama::{model}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        data = self._show(model)
        if data is None:
            return None
        caps = self.from_show(model, data)
        _cache_put(key, caps)
        return caps


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
    """Wave 9.4 — reads the MLX checkpoint's config.json to derive capabilities.
    vision ← `vision_config`; embedding ← model_type/architectures; native_ctx ←
    max_position_embeddings; quantization ← `quantization.bits`. Only a cheap
    config.json fetch (never the whole model)."""
    provider = "mlx"

    def probe(self, model: str) -> Optional[ProbedCapabilities]:
        if not model:
            return None
        try:
            from huggingface_hub import hf_hub_download
            import json
            cfg = json.load(open(hf_hub_download(model, "config.json")))
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
_OLLAMA_PROBE = OllamaCapabilityProbe()


def probe_capabilities(model: str, provider: str = "ollama") -> Optional[ProbedCapabilities]:
    """Resolve capabilities for a model, ENGINE-FIRST.

    Order: ask the engine (Ollama /api/show today; MLX later) → if it answers,
    that IS the truth. The registry is consulted only to OVERRIDE explicit pins
    or fill gaps the engine can't report (curated display niceties), never to
    gate or to flip an engine-reported capability off.
    """
    if not model:
        return None
    caps: Optional[ProbedCapabilities] = None
    if provider == "mlx":
        caps = MLXCapabilityProbe().probe(model)
    else:
        caps = _OLLAMA_PROBE.probe(model)

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
