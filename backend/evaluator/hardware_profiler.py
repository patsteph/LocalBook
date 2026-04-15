"""Hardware profiler — detects Apple Silicon specs for benchmarking context.

Uses macOS system_profiler and sysctl to capture chip, core counts, GPU cores,
RAM, and OS version. Caches result for process lifetime.
"""

import platform
import subprocess
import re
from evaluator.models import HardwareProfile
import logging
logger = logging.getLogger(__name__)

_cached_profile: HardwareProfile | None = None


def get_hardware_profile() -> HardwareProfile:
    """Detect and return the current machine's hardware profile.
    
    Cached after first call — hardware doesn't change mid-session.
    """
    global _cached_profile
    if _cached_profile is not None:
        return _cached_profile

    profile = HardwareProfile()

    # ── Chip + Core info via system_profiler ──────────────────────────────
    try:
        sp = subprocess.run(
            ["system_profiler", "SPHardwareDataType"],
            capture_output=True, text=True, timeout=10
        )
        output = sp.stdout

        # Chip Name: Apple M4
        chip_match = re.search(r"Chip:\s*(.+)", output)
        if chip_match:
            profile.chip = chip_match.group(1).strip()

        # Total Number of Cores: 10 (4 performance and 6 efficiency)
        cores_match = re.search(
            r"Total Number of Cores:\s*(\d+)\s*\((\d+)\s*performance\s*and\s*(\d+)\s*efficiency\)",
            output
        )
        if cores_match:
            profile.total_cores = int(cores_match.group(1))
            profile.performance_cores = int(cores_match.group(2))
            profile.efficiency_cores = int(cores_match.group(3))
        else:
            # Fallback: just get total cores
            cores_simple = re.search(r"Total Number of Cores:\s*(\d+)", output)
            if cores_simple:
                profile.total_cores = int(cores_simple.group(1))

        # Memory: 16 GB
        mem_match = re.search(r"Memory:\s*(\d+)\s*GB", output)
        if mem_match:
            profile.memory_gb = int(mem_match.group(1))

    except Exception as e:
        print(f"[HW-PROFILER] system_profiler failed: {e}")

    # ── GPU cores via sysctl ─────────────────────────────────────────────
    try:
        gpu_result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.gpu_core_count"],
            capture_output=True, text=True, timeout=5
        )
        if gpu_result.returncode == 0 and gpu_result.stdout.strip():
            profile.gpu_cores = int(gpu_result.stdout.strip())
    except Exception:
        # Try IORegistry fallback
        try:
            ioreg = subprocess.run(
                ["ioreg", "-l", "-w0"],
                capture_output=True, text=True, timeout=10
            )
            gpu_match = re.search(r'"gpu-core-count"\s*=\s*(\d+)', ioreg.stdout)
            if gpu_match:
                profile.gpu_cores = int(gpu_match.group(1))
        except Exception as _e:
            logger.debug(f"[hardware-profiler] {type(_e).__name__}: {_e}")

    # ── Metal support (always True on Apple Silicon) ─────────────────────
    profile.metal_support = "Apple" in profile.chip if profile.chip else True

    # ── OS version ───────────────────────────────────────────────────────
    profile.os_version = platform.mac_ver()[0] or platform.platform()

    # ── Ollama version ───────────────────────────────────────────────────
    try:
        ollama_v = subprocess.run(
            ["ollama", "version"],
            capture_output=True, text=True, timeout=5
        )
        if ollama_v.returncode == 0:
            # Output: "ollama version is 0.6.2" or just "0.6.2"
            ver_text = ollama_v.stdout.strip()
            ver_match = re.search(r"(\d+\.\d+\.\d+)", ver_text)
            if ver_match:
                profile.ollama_version = ver_match.group(1)
            else:
                profile.ollama_version = ver_text
    except Exception:
        profile.ollama_version = "unknown"

    # ── Derive tier from RAM ─────────────────────────────────────────────
    profile.derive_tier()

    _cached_profile = profile
    return profile


def reset_cache():
    """Reset cached profile (for testing only)."""
    global _cached_profile
    _cached_profile = None
