"""Auto-derived per-model RunProfile (Locker rebuild — build B).

The evaluator used to run every model with olmo/gemma-tuned generation options, so
anything else scored badly. A RunProfile is the missing per-(model, engine) layer,
DERIVED from the engine with zero hand-curation:
  • thinking      — is the model a reasoner, and should reasoning be on for a run?
  • stop_sequences — the model's own stops (from its modelfile), not our olmo defaults
  • template      — where the chat template lives (Ollama bakes its own; MLX reads
                    tokenizer_config.json OR a standalone chat_template.jinja — the
                    Gemma4 gotcha, handled in build D)
  • normalization — always strip reasoning defensively before scoring (Qwen3 emits
                    <think> blocks even when "off")

Build C consumes this: it applies `to_ollama_options()` when generating and the
`filter_list` (see output_filters) when scoring. Engine-agnostic by construction —
MLX derivation slots in behind the same `derive_run_profile` dispatch.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RunProfile:
    model: str
    provider: str = "ollama"
    source: str = "derived"                       # derived | derived+fallback | default
    thinking_capable: bool = False
    thinking_enabled: bool = False                # eval default: OFF (score the answer)
    thinking_param: Optional[str] = None          # Ollama `think`: bool or level string
    stop_sequences: list = field(default_factory=list)
    template_source: str = "ollama-baked"         # ollama-baked | hf-tokenizer | hf-jinja | none
    # Normalization filters applied to raw output BEFORE scoring (output_filters keys).
    # strip_thinking is ALWAYS present — Qwen3 emits <think> even when disabled.
    normalize_filters: list = field(default_factory=lambda: ["strip_thinking"])

    def to_dict(self) -> dict:
        return asdict(self)

    def to_ollama_options(self, base_options: Optional[dict] = None) -> dict:
        """Produce the generation options dict for an Ollama call. Merges the
        model's own stop sequences into any caller-provided options (union, no
        dupes) and returns `think` separately-aware callers can pass at the top
        level (Ollama's `think` is a request field, not an option)."""
        opts = dict(base_options or {})
        stops = list(opts.get("stop") or [])
        for s in self.stop_sequences:
            if s not in stops:
                stops.append(s)
        if stops:
            opts["stop"] = stops
        return opts


# ── Stop-sequence extraction from an Ollama /api/show `parameters` blob ───────────
# The blob looks like:  stop "<|im_start|>"\nstop "<|im_end|>"\ntemperature 0.7
_STOP_RE = re.compile(r'^\s*stop\s+"?(.*?)"?\s*$', re.MULTILINE)


def _parse_stops(parameters: str) -> list[str]:
    if not parameters:
        return []
    seen: list[str] = []
    for m in _STOP_RE.finditer(parameters):
        val = m.group(1)
        if val and val not in seen:
            seen.append(val)
    return seen


def derive_run_profile(model: str, provider: str = "ollama", caps=None) -> RunProfile:
    """Derive a RunProfile by asking the engine. `caps` (a ProbedCapabilities) may
    be passed to avoid a redundant probe. Never raises — falls back to a safe
    default profile when the engine is unreachable."""
    if provider == "mlx":
        # Build D: read tokenizer_config.json chat_template OR chat_template.jinja,
        # plus generation_config for thinking/stops. Stubbed for now.
        return RunProfile(model=model, provider="mlx", source="default", template_source="none")

    # ── Ollama ──
    if caps is None:
        try:
            from evaluator.capability_probe import probe_capabilities
            caps = probe_capabilities(model)
        except Exception:
            caps = None
    thinking_capable = bool(getattr(caps, "thinking", False))

    stops: list[str] = []
    source = "derived"
    try:
        from evaluator.capability_probe import ollama_show
        show = ollama_show(model)
        if show is not None:
            stops = _parse_stops(str(show.get("parameters") or ""))
        else:
            source = "derived+fallback"
    except Exception:
        source = "derived+fallback"

    return RunProfile(
        model=model,
        provider="ollama",
        source=source,
        thinking_capable=thinking_capable,
        # For evaluation we score the FINAL answer, so reasoning is OFF by default
        # (deterministic, faster). A task may flip it on; the normalizer strips
        # <think> either way.
        thinking_enabled=False,
        thinking_param=("false" if thinking_capable else None),
        stop_sequences=stops,
        template_source="ollama-baked",
        normalize_filters=["strip_thinking"],
    )
