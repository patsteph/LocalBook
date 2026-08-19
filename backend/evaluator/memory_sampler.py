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
            "path": self.path,
        }


def default_path(run_id: str, kind: str = "eval") -> str:
    from config import settings
    return os.path.join(str(settings.data_dir), "eval_results", "memory",
                        f"{kind}-{run_id}.jsonl")
