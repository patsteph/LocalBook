"""Memory sampler — what an eval run or a soak actually costs in RAM.

Stage 2.3 of the MLX cutover build order. Without this, the 16 GB question ("does MLX-only stay
healthy over hours with no eviction?") has no evidence behind it, and the A/B can compare quality
and speed but not the resource that actually constrains the shipping floor.

Two design points, both learned the hard way on this project:

1. **APPENDS TO DISK, every sample.** A soak that ends in a swap-death or a GPU watchdog reboot
   destroys anything held in memory — and those are precisely the runs whose data matters most.
   An in-memory buffer flushed at the end would lose exactly the interesting cases.
2. **Samples on a daemon thread, never the event loop.** The loop is what we are measuring; a
   sampler that blocks it would distort its own readings (and this repo has a documented history
   of blocking-on-loop bugs).

`mx.get_peak_memory()` is MLX's own high-water mark and is the only reading that attributes
memory to MLX specifically; RSS and system-wide pressure are needed too because MLX is not the
only thing on the machine (Kokoro, whisper, the reranker and Ollama all live outside its budget).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GB = 1024 ** 3


def _mlx_readings() -> Dict[str, Optional[float]]:
    """MLX's own accounting. Never imports mlx at module scope (thread-affinity: mlx-lm#1256)."""
    out: Dict[str, Optional[float]] = {"mlx_active_gb": None, "mlx_peak_gb": None, "mlx_cache_gb": None}
    try:
        import mlx.core as mx
        out["mlx_active_gb"] = round(mx.get_active_memory() / GB, 3)
        out["mlx_peak_gb"] = round(mx.get_peak_memory() / GB, 3)
        try:
            out["mlx_cache_gb"] = round(mx.get_cache_memory() / GB, 3)
        except Exception:
            pass
    except Exception:
        pass
    return out


def _system_readings() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        import psutil
        p = psutil.Process(os.getpid())
        out["rss_gb"] = round(p.memory_info().rss / GB, 3)
        vm = psutil.virtual_memory()
        out["system_used_gb"] = round((vm.total - vm.available) / GB, 3)
        out["system_available_gb"] = round(vm.available / GB, 3)
        sw = psutil.swap_memory()
        # Cumulative counters — the DELTA across a run is the meaningful number. Sustained
        # swap-out on Apple Silicon is the symptom that precedes the bad outcomes.
        out["swap_out_total"] = int(getattr(sw, "sout", 0) or 0)
        out["swap_used_gb"] = round(sw.used / GB, 3)
    except Exception:
        pass
    return out


def sample_once(label: str = "") -> Dict[str, Any]:
    s: Dict[str, Any] = {"ts": time.time(), "label": label}
    s.update(_system_readings())
    s.update(_mlx_readings())
    return s


class MemorySampler:
    """Background sampler appending one JSON object per line."""

    def __init__(self, path: str, interval_s: float = 1.0, label: str = ""):
        self.path = path
        self.interval_s = max(0.2, float(interval_s))
        self.label = label
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._samples: List[Dict[str, Any]] = []
        self._first: Optional[Dict[str, Any]] = None

    def _run(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                s = sample_once(self.label)
                if self._first is None:
                    self._first = s
                self._samples.append(s)
                with open(self.path, "a") as fh:
                    fh.write(json.dumps(s) + "\n")
            except Exception as e:      # a sampler must never take the run down with it
                logger.debug(f"[mem-sampler] sample failed: {e}")
            self._stop.wait(self.interval_s)

    def start(self) -> "MemorySampler":
        if self._thread:
            return self
        self._thread = threading.Thread(target=self._run, name="mem-sampler", daemon=True)
        self._thread.start()
        logger.info(f"[mem-sampler] sampling every {self.interval_s}s → {self.path}")
        return self

    def stop(self) -> Dict[str, Any]:
        """Stop and return the summary an eval/soak should record."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_s * 3)
            self._thread = None
        return self.summary()

    def summary(self) -> Dict[str, Any]:
        if not self._samples:
            return {"samples": 0}

        def _peak(k: str) -> Optional[float]:
            vals = [s[k] for s in self._samples if s.get(k) is not None]
            return max(vals) if vals else None

        def _last(k: str) -> Optional[float]:
            for s in reversed(self._samples):
                if s.get(k) is not None:
                    return s[k]
            return None

        # ── The two GATE metrics (2026-08-19, from the memory-metrics investigation) ──
        # `peak_rss_gb` is RETIRED as a gate: MLX's Metal buffers are billed to XNU's
        # iokit_mapped ledger and contribute ZERO to psutil RSS — 3 GiB of mx.zeros moves RSS
        # by −1 GB, and r(rss, mlx_active) = −0.207 across the real runs. It stayed a
        # diagnostic; it is not evidence about MLX memory.
        #
        # PRIMARY — system headroom as DURATION, not a single dip. Every run of both engines
        # touches 0.29-0.74 GB available at some instant, so a single-dip criterion cannot
        # discriminate; time spent under pressure can.
        avail = [s["system_available_gb"] for s in self._samples
                 if s.get("system_available_gb") is not None]
        n = len(avail) or 1
        bands = {f"pct_below_{t}": round(100.0 * sum(1 for v in avail if v < t) / n, 1)
                 for t in (0.5, 1.0, 1.5, 2.0, 3.0)}
        longest = cur = 0
        for v in avail:
            cur = cur + 1 if v < 1.5 else 0
            longest = max(longest, cur)
        # Samples are ~1 Hz; scale by the real cadence so a changed interval stays honest.
        span = (self._samples[-1]["ts"] - self._samples[0]["ts"]) or 1.0
        per_sample_s = span / max(1, len(self._samples) - 1)
        longest_s = round(longest * per_sample_s, 1)

        # SECONDARY — MLX's own commitment against Apple's working set, as a FRACTION so the
        # threshold is portable to a 32/64 GB Mac. active+cache, because the allocator cache is
        # committed memory the process is holding even when not in use.
        committed = [(s.get("mlx_active_gb") or 0) + (s.get("mlx_cache_gb") or 0)
                     for s in self._samples
                     if s.get("mlx_active_gb") is not None]
        peak_committed = round(max(committed), 3) if committed else None
        ws = limit = None
        try:
            from services.model_sizing import working_set_gb
            ws = working_set_gb() or None
            # The ceiling mlx_engine actually enforces — see the GATE note.
            _env = os.environ.get("LOCALBOOK_MLX_MEMORY_LIMIT_GB")
            limit = float(_env) if _env else (round(ws * 0.90, 2) if ws else None)
        except Exception:
            pass
        committed_frac = (round(peak_committed / limit, 3)
                          if (peak_committed and limit) else None)

        first, last = self._samples[0], self._samples[-1]
        swap_delta = None
        if first.get("swap_out_total") is not None and last.get("swap_out_total") is not None:
            swap_delta = int(last["swap_out_total"] - first["swap_out_total"])
        return {
            "samples": len(self._samples),
            "duration_s": round(last["ts"] - first["ts"], 1),
            "peak_rss_gb": _peak("rss_gb"),
            "peak_system_used_gb": _peak("system_used_gb"),
            "min_system_available_gb": min(
                (s["system_available_gb"] for s in self._samples
                 if s.get("system_available_gb") is not None), default=None),
            "mlx_peak_gb": _peak("mlx_peak_gb"),
            "mlx_active_end_gb": _last("mlx_active_gb"),
            # ⚠️ The eviction question in one number. MLX never frees `_resident`, so if
            # `mlx_active_end_gb` does not fall after a pipeline completes, nothing was
            # released — which is exactly what the cutover needs to prove or disprove.
            "mlx_active_start_gb": next(
                (s["mlx_active_gb"] for s in self._samples if s.get("mlx_active_gb") is not None),
                None),
            "swap_out_delta": swap_delta,
            "sustained_swap": bool(swap_delta and swap_delta > 0),
            # ── GATE METRICS ──
            "min_system_available_gb_gate": min(avail) if avail else None,
            **bands,
            "longest_s_below_1_5gb": longest_s,
            "peak_mlx_committed_gb": peak_committed,
            "working_set_gb": round(ws, 2) if ws else None,
            "mlx_memory_limit_gb": limit,
            "mlx_committed_frac_of_limit": committed_frac,
            # Verdict against the thresholds fixed BEFORE the numbers existed.
            "gate": _verdict(min(avail) if avail else None, bands.get("pct_below_1.5"),
                             longest_s, committed_frac),
            # Kept as a DIAGNOSTIC only — see the note above. Valid only when the machine had
            # >4 GB free; below that the compressor makes RSS actively misleading.
            "peak_rss_gb_diagnostic_only": _peak("rss_gb"),
            "path": self.path,
        }


# Thresholds from memory-metrics §3.2, with TWO CORRECTIONS made after validating them
# against all seven recorded runs — the recommendation as written failed every run,
# including both Ollama baselines, so it discriminated nothing.
#
# 1. NO single-dip floor. §3.2 recommended `min_available >= 0.75 GB` while its own analysis
#    said "every one of the six runs — Ollama AND MLX — touches 0.29-0.74 GB available at
#    some instant [so] a single-dip criterion cannot discriminate." Both statements cannot
#    hold. The duration clauses DO discriminate (Ollama 0.3-6.0% vs MLX 5.9-22.8% of samples
#    under 1.5 GB), so the floor is reported as a diagnostic and gates nothing.
# 2. The MLX ceiling is measured against the CONFIGURED LIMIT, not the working set. All four
#    MLX runs peak at exactly 10.66 GiB — which is `mlx_engine`'s own
#    `set_memory_limit(0.90 * working_set)`. Aborting above "90% of working set" is aborting
#    on the limit we set ourselves: circular, and guaranteed to fire. Sitting AT the limit
#    means MLX wanted more and was capped — informative, not dangerous, since the cap is what
#    prevents the danger. It warns.
GATE = {
    "pct_below_1_5_max": 15.0,        # sustained-pressure budget
    "longest_s_below_1_5_max": 30.0,  # no long stalls
    "min_available_report_gb": 0.75,  # DIAGNOSTIC ONLY — does not gate
    "committed_frac_of_limit_warn": 0.98,   # pinned at our own ceiling
}


def _verdict(min_avail, pct_1_5, longest_s, committed_frac_of_limit) -> Dict[str, Any]:
    """pass / warn / abort, with the reasons named rather than left to a reader.

    ABORT is reserved for SUSTAINED system pressure — the thing that actually precedes
    swap-death and the watchdog. Everything else warns.
    """
    reasons, level = [], "pass"
    if pct_1_5 is not None and pct_1_5 > GATE["pct_below_1_5_max"]:
        reasons.append(f"{pct_1_5}% of samples under 1.5 GB free "
                       f"(max {GATE['pct_below_1_5_max']}%)")
        level = "abort"
    if longest_s is not None and longest_s > GATE["longest_s_below_1_5_max"]:
        reasons.append(f"{longest_s}s continuously under 1.5 GB "
                       f"(max {GATE['longest_s_below_1_5_max']}s)")
        level = "abort"
    if min_avail is not None and min_avail < GATE["min_available_report_gb"]:
        # Diagnostic: every run of both engines does this, so it cannot be a gate.
        reasons.append(f"[info] touched {min_avail:.2f} GB available at some instant")
    if committed_frac_of_limit is not None and \
            committed_frac_of_limit >= GATE["committed_frac_of_limit_warn"]:
        reasons.append(f"MLX pinned at {committed_frac_of_limit:.0%} of its configured memory "
                       f"limit — it wanted more and was capped")
        if level == "pass":
            level = "warn"
    return {"level": level, "reasons": reasons}


def default_path(run_id: str, kind: str = "eval") -> str:
    from config import settings
    return os.path.join(str(settings.data_dir), "eval_results", "memory",
                        f"{kind}-{run_id}.jsonl")
