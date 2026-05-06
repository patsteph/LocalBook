"""LLM Locker / Universal Switcher Service
Provides safety guardrails for dynamically swapping active models.
Enforces RAM limits, adjusts context sizing, and prunes unused dependent models.
"""

import logging
from typing import Tuple, Dict, Any, Optional
from config import settings
from evaluator.hardware_profiler import get_hardware_profile
from evaluator.model_registry import ModelRegistry

logger = logging.getLogger(__name__)
registry = ModelRegistry()


def _get_default_vision_model() -> str:
    """
    Return the best available standalone vision model from the registry.
    Prefers Granite 3.3 Vision (current production target). 3.2 is kept
    as a fallback only — it produces noticeably worse OCR (spurious
    tables, hallucinated structure) and is being phased out.
    """
    # Order: IBM-namespaced 3.3 (the user's pinned target) → plain 3.3 →
    # legacy 3.2. Whichever the user has pulled wins.
    for model_name in [
        "ibm/granite3.3-vision:2b",
        "granite3.3-vision:2b",
        "granite3.2-vision:2b",
    ]:
        if registry.get_model(model_name):
            return model_name
    return "ibm/granite3.3-vision:2b"


class ModelSwapError(Exception):
    """Raised when a model swap request violates safety bounds."""
    pass

class LLMLocker:
    """Safely manages universal model switching."""
    
    @classmethod
    def _live_model_info(cls, ollama_name: str) -> Optional[Dict[str, Any]]:
        """
        Query Ollama /api/show for a model that is not in the static registry.
        Returns a minimal dict with size_gb, ram_required_gb, supports_vision.
        Returns None if Ollama is unreachable or model is unknown.
        """
        import urllib.request
        from config import settings as _s
        try:
            import json as _json
            req = urllib.request.Request(
                f"{_s.ollama_base_url}/api/show",
                data=_json.dumps({"name": ollama_name}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode())
            size_bytes = data.get("size", 0)
            size_gb = size_bytes / (1024 ** 3)
            ram_gb = round(size_gb * 1.3, 1)
            return {
                "size_gb": size_gb,
                "ram_required_gb": ram_gb,
                "supports_vision": False,  # conservative default for unknown models
            }
        except Exception:
            return None

    @classmethod
    def analyze_swap(cls, target_ollama_name: str, role: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Analyze if a swap to target_model is safe on current hardware.
        role: "main_model", "fast_model", or "vision_model"

        Models not in the static registry are allowed — live Ollama data is
        used to estimate RAM requirements so users can freely test any model
        they have pulled.

        Returns: (is_safe, message, recommended_changes)
        recommended_changes is a dict of config shifts (e.g. dropping vision model if main model includes it)
        """
        model_info = registry.get_model(target_ollama_name)
        _live: Optional[Dict[str, Any]] = None

        # v1.8.0 (Phase 2): llama-server sidecar swaps are permitted. The API
        # layer is expected to call `sidecar_manager.ensure_started()` before
        # invoking analyze_swap; we still defend here with a cheap health
        # check so a ghost request can't silently swap to a dead backend.
        if model_info and getattr(model_info, "provider", "ollama") == "llama_server":
            try:
                from services.llm_provider import health_check_sync, Provider
                if not health_check_sync(Provider.LLAMA_SERVER):
                    return (
                        False,
                        f"llama-server sidecar is not responding on the configured port. "
                        f"Click 'Start' in the Locker's Sidecar panel or check the binary/model paths.",
                        {},
                    )
            except Exception as _e:
                logger.debug(f"[LLMLocker] sidecar health probe failed: {_e}")
                return (False, "Could not probe llama-server health.", {})

        if not model_info:
            # Not in registry — query Ollama live instead of hard-blocking
            _live = cls._live_model_info(target_ollama_name)
            if _live is None:
                return (
                    False,
                    f"Model '{target_ollama_name}' is not installed in Ollama or Ollama is unreachable.",
                    {},
                )
            logger.info(f"[LLMLocker] '{target_ollama_name}' not in registry — using live Ollama data.")
        else:
            if role not in model_info.supported_roles:
                # Still allow the swap — roles in registry are advisory, not a hard gate
                logger.warning(
                    f"[LLMLocker] '{target_ollama_name}' not listed for role '{role}' in registry — allowing anyway."
                )
            
        hw = get_hardware_profile()
        sys_ram = hw.memory_gb
        
        # Calculate memory delta
        current_main = settings.ollama_model
        current_fast = getattr(settings, 'ollama_fast_model', "")
        current_vision = getattr(settings, 'vision_model', "")
        
        # We need a rough estimate of currently loaded required RAM
        # If we are swapping main, we subtract current_main's RAM and add target's RAM
        target_ram = model_info.min_ram_gb if model_info else int(_live["ram_required_gb"])
        
        if role == "main_model":
            changes_key = "ollama_model"
        elif role == "fast_model":
            changes_key = "ollama_fast_model"
        elif role == "embedding_model":
            changes_key = "embedding_model"
        elif role == "vision_model":
            changes_key = "vision_model"
        else:
            changes_key = role
            
        changes = {
            changes_key: target_ollama_name
        }
        
        if model_info and getattr(model_info, 'embedding_dim', 0) > 0:
            changes["embedding_dim"] = model_info.embedding_dim

        # Determine if target supports vision
        _supports_vision = (model_info.supports_vision if model_info else (_live or {}).get("supports_vision", False))

        # Vision Collapse / Restoration Logic
        if role == "main_model":
            if _supports_vision:
                # Main model handles vision natively — collapse to 2-model setup
                # Set vision_model to the main model itself so downstream code uses it
                changes["vision_model"] = target_ollama_name
                msg = (f"Safe to swap to {target_ollama_name}. NOTE: This model natively supports vision. "
                       f"Vision tasks will now be handled by the main model (no standalone vision model needed).")
            else:
                # Main model does NOT support vision — ensure standalone vision model is active
                current_vision = settings.vision_model
                current_main = settings.ollama_model
                # If vision was previously collapsed to the old main model, restore default
                if current_vision == current_main:
                    default_vision = _get_default_vision_model()
                    changes["vision_model"] = default_vision
                    msg = (f"Safe to swap to {target_ollama_name}. NOTE: Restoring standalone vision model "
                           f"({default_vision}) since this model does not support vision natively.")
                else:
                    msg = f"Safe to swap to {target_ollama_name}."
        else:
            msg = f"Safe to swap to {target_ollama_name}."
            
        # Hard cap guardrail — single-model check
        if target_ram > sys_ram:
            return False, f"INSUFFICIENT UNIFIED MEMORY. {target_ollama_name} requires minimum {target_ram}GB RAM. Your Mac has {sys_ram}GB.", {}

        # Combined RAM headroom check — estimate VRAM footprint for concurrent models
        # On Apple Silicon, Ollama uses unified memory. Models are swapped in/out,
        # so typically only 2 models are loaded simultaneously (main + one of fast/vision).
        # We estimate concurrent VRAM as disk_size * 1.2 (weights + KV cache), NOT min_ram_gb
        # which is the standalone system requirement and already includes OS overhead.
        OS_HEADROOM_GB = 3  # macOS, app, embeddings, system services
        
        def _model_vram(name: str) -> float:
            """Estimate actual VRAM footprint for a loaded model."""
            # If this is the target we already have live data for, use it
            if name == target_ollama_name and _live:
                return _live["size_gb"] * 1.2
            info = registry.get_model(name)
            if info and info.disk_size_gb > 0:
                return info.disk_size_gb * 1.2  # weights + KV cache overhead
            return 3.0  # conservative default for unknown models without live data
        
        if role == "main_model":
            main_vram = _model_vram(target_ollama_name)
            # Ollama rotates between fast/vision — use the larger one as concurrent estimate
            final_vision = changes.get("vision_model", current_vision)
            secondary_vram = max(
                _model_vram(current_fast),
                _model_vram(final_vision) if final_vision != target_ollama_name else 0
            )
            combined_vram = main_vram + secondary_vram
        elif role == "fast_model":
            main_vram = _model_vram(current_main)
            fast_vram = _model_vram(target_ollama_name)
            # Vision rotates with fast — use the larger of fast/vision as secondary
            vision_vram = _model_vram(current_vision) if current_vision != current_main else 0
            combined_vram = main_vram + max(fast_vram, vision_vram)
        else:
            combined_vram = _model_vram(current_main) + max(
                _model_vram(current_fast), _model_vram(target_ollama_name)
            )

        if combined_vram + OS_HEADROOM_GB > sys_ram:
            return False, (
                f"INSUFFICIENT MEMORY HEADROOM. Estimated concurrent VRAM = {combined_vram:.1f}GB + "
                f"{OS_HEADROOM_GB}GB OS headroom = {combined_vram + OS_HEADROOM_GB:.1f}GB, but your Mac has {sys_ram}GB. "
                f"This combination would likely cause crashes or extreme swapping."
            ), {}

        # Warning cap for recommended RAM
        if model_info and model_info.recommended_ram_gb > sys_ram:
            msg += f" WARNING: This model heavily bottlenecks on {sys_ram}GB and is recommended for {model_info.recommended_ram_gb}GB+ systems."

        # Context extraction
        if model_info and hasattr(model_info, 'context_window'):
            changes["MAX_RAG_CONTEXT"] = min(model_info.context_window, 131072)
            
        return True, msg, changes

    @classmethod
    def execute_swap(cls, target_ollama_name: str, role: str) -> str:
        """
        Executes the swap physically into the environment and config states.
        """
        is_safe, message, changes = cls.analyze_swap(target_ollama_name, role)
        
        if not is_safe:
            raise ModelSwapError(message)
            
        # Write changes to the config environment
        cls._patch_environment(changes)
        
        return message
        
    @classmethod
    def _patch_environment(cls, changes: Dict[str, Any]):
        """Persists changes back to the .env file and reloads config settings."""
        from pathlib import Path
        env_path = Path(".env")
        
        lines = []
        if env_path.exists():
            lines = env_path.read_text().splitlines()
            
        env_dict = {}
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env_dict[k.strip()] = v.strip()
                
        # Apply the changes
        for k, v in changes.items():
            key_map = {
                "ollama_main_model": "LOCALBOOK_OLLAMA_MODEL",
                "ollama_model": "LOCALBOOK_OLLAMA_MODEL",
                "ollama_fast_model": "LOCALBOOK_OLLAMA_FAST_MODEL",
                "vision_model": "LOCALBOOK_VISION_MODEL",
                "embedding_model": "LOCALBOOK_EMBEDDING_MODEL",
                "embedding_dim": "LOCALBOOK_EMBEDDING_DIM",
                "MAX_RAG_CONTEXT": "LOCALBOOK_MAX_RAG_CONTEXT"
            }
            env_key = key_map.get(k, k.upper())
            
            if v is None:
                # Remove it instead of writing 'None'
                if env_key in env_dict:
                    del env_dict[env_key]
            else:
                env_dict[env_key] = str(v)
                
        # Sync back to memory 
        if "ollama_model" in changes:
            settings.ollama_model = changes["ollama_model"]
        if "ollama_fast_model" in changes:
            setattr(settings, 'ollama_fast_model', changes["ollama_fast_model"])
        if "vision_model" in changes:
            setattr(settings, 'vision_model', changes["vision_model"])
        if "embedding_model" in changes:
            setattr(settings, 'embedding_model', changes["embedding_model"])
        if "embedding_dim" in changes:
            setattr(settings, 'embedding_dim', int(changes["embedding_dim"]))
            
        # Invalidate the settings/ollama/models cache so the next fetch reflects changes
        try:
            from api.settings import _ollama_models_cache, _ollama_models_lock
            import threading
            with _ollama_models_lock:
                _ollama_models_cache["ts"] = None
                _ollama_models_cache["data"] = None
        except Exception:
            pass  # non-fatal — cache will expire naturally after 30s

        # Write to .env
        output_lines = [f"{k}={v}" for k, v in env_dict.items()]
        env_path.write_text("\n".join(output_lines))
        logger.info(f"Environment patched with: {changes}")

locker = LLMLocker()
