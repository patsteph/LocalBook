"""LLM Locker / Universal Switcher Service
Provides safety guardrails for dynamically swapping active models.
Enforces RAM limits, adjusts context sizing, and prunes unused dependent models.
"""

import logging
from typing import Tuple, Dict, Any, Optional
from config import settings
from evaluator.hardware_profiler import get_hardware_profile
# The shared singleton, NOT a second instance. Two registries meant two caches, two
# refresh cycles, and a swap that updated one while the Locker UI read the other.
from evaluator.model_registry import model_registry as registry

logger = logging.getLogger(__name__)


def _get_default_vision_model() -> str:
    """
    Return the best available standalone vision model from the registry.

    Selection order is conservative on purpose: we pick a model that we
    KNOW the current Ollama runner can serve, and treat newer-but-flaky
    models as opt-in via Settings → Models.

    granite3.2-vision:2b is the stable floor. granite3.3 was removed after
    Ollama 0.23.x llama-runner segfaults on Apple Silicon. Users can select
    any installed vision model (gemma3:4b, gemma4:e2b, llava, moondream)
    via the Vision column in the LLM Selector.
    """
    if registry.get_model("granite3.2-vision:2b"):
        return "granite3.2-vision:2b"
    return "granite3.2-vision:2b"


class ModelSwapError(Exception):
    """Raised when a model swap request violates safety bounds."""
    pass

class LLMLocker:
    """Safely manages universal model switching."""
    
    @classmethod
    def _live_model_info(cls, model_id: str) -> Optional[Dict[str, Any]]:
        """Describe a model that is NOT in the static registry, from the local cache.

        Was a POST to Ollama's /api/show. With Ollama gone that always returned None, and
        `analyze_swap` turns None into a hard block — so ANY model absent from
        known_models.json became un-selectable. That matters directly for the model
        browser: a freshly downloaded MLX checkpoint has no registry row by definition.

        Everything here is read off disk: exact weight bytes from the checkpoint, capability
        flags from the cached config.json. Returns None only when the model genuinely is not
        downloaded.
        """
        try:
            from services.model_presence import is_present
            from services.model_sizing import exact_weight_gb

            if not is_present(model_id):
                return None
            size_gb = exact_weight_gb(model_id) or 0.0

            supports_vision = False
            try:
                from evaluator.capability_probe import probe_capabilities
                pc = probe_capabilities(model_id, provider="mlx")
                if pc:
                    supports_vision = bool(pc.vision)
            except Exception:
                pass

            return {
                "size_gb": size_gb,
                # Weights plus room for KV and activations. `model_sizing.fit()` is the
                # precise answer; this stays a cheap estimate because the caller only uses
                # it for a headroom sanity check.
                "ram_required_gb": round(size_gb * 1.3, 1),
                "supports_vision": supports_vision,
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

        if not model_info:
            # Not in registry — query Ollama live instead of hard-blocking
            _live = cls._live_model_info(target_ollama_name)
            if _live is None:
                return (
                    False,
                    f"Model '{target_ollama_name}' is not downloaded. Get it from LLM Studio first.",
                    {},
                )
            logger.info(f"[LLMLocker] '{target_ollama_name}' not in registry — using on-disk data.")
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
        # When main or fast model supports vision natively, collapse vision_model to it.
        # When switching away to a non-vision model, restore the standalone default.
        if role == "main_model":
            if _supports_vision:
                changes["vision_model"] = target_ollama_name
                msg = (f"Safe to swap to {target_ollama_name}. NOTE: This model natively supports vision. "
                       f"Vision tasks will now be handled by the main model (no standalone vision model needed).")
            else:
                current_vision = settings.vision_model
                current_main = settings.ollama_model
                if current_vision == current_main:
                    default_vision = _get_default_vision_model()
                    changes["vision_model"] = default_vision
                    msg = (f"Safe to swap to {target_ollama_name}. NOTE: Restoring standalone vision model "
                           f"({default_vision}) since this model does not support vision natively.")
                else:
                    msg = f"Safe to swap to {target_ollama_name}."
        elif role == "fast_model":
            if _supports_vision:
                changes["vision_model"] = target_ollama_name
                msg = (f"Safe to swap to {target_ollama_name}. NOTE: This model natively supports vision. "
                       f"Vision tasks will now be handled by the fast model.")
            else:
                current_vision = settings.vision_model
                current_fast = settings.ollama_fast_model
                if current_vision == current_fast:
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

        # Combined memory headroom for concurrently-resident models.
        #
        # This used to be `disk_size_gb * 1.2` from the registry — a declared size times a
        # magic factor, which is exactly the guesswork `model_sizing` replaced (the old
        # estimator was off by −16 % to +99 %, and reported 0.0 GB for both arctic builds,
        # which read as "fits"). Weights are now read from the checkpoint.
        #
        # The concurrency model changed too. The old comment reasoned "Ollama swaps models
        # in/out, so typically only 2 are loaded" — MLX has no such rotation and holds every
        # loaded model until something evicts it, so assuming 2 UNDERSTATES the footprint.
        OS_HEADROOM_GB = 3  # macOS, app, embeddings, system services
        
        def _model_vram(name: str) -> float:
            """Resident cost of a loaded model, in GB: exact weights + activation slack."""
            if not name:
                return 0.0
            try:
                from services.model_sizing import exact_weight_gb
                w = exact_weight_gb(name)
                if w:
                    # 1.2× for activations/scratch, matching model_sizing.fit's factor. KV is
                    # excluded deliberately — it scales with context, and this check is about
                    # whether the SET of models can co-reside at all.
                    return round(w * 1.2, 2)
            except Exception:
                pass
            info = registry.get_model(name)
            if info and info.disk_size_gb > 0:
                return info.disk_size_gb * 1.2
            return 3.0  # conservative default for an unknown, unmeasurable model
        
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
            # Use post-collapse vision value: if fast model supports vision, vision is now the fast model itself
            final_vision = changes.get("vision_model", current_vision)
            vision_vram = _model_vram(final_vision) if final_vision not in (current_main, target_ollama_name) else 0
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

        # Context extraction (P7: informational only). This is the model's NATIVE
        # window shown in the swap summary. The window the app actually uses at
        # runtime is set by llm_runtime.effective_num_ctx_cap (RAM-tier-aware) +
        # compute_num_ctx per call — NOT by this value. No code reads
        # LOCALBOOK_MAX_RAG_CONTEXT; it's a display/record field, so keep it as the
        # native ceiling and let the runtime cap govern.
        if model_info and hasattr(model_info, 'context_window'):
            changes["MAX_RAG_CONTEXT"] = min(model_info.context_window, 131072)

        return True, msg, changes

    @classmethod
    def execute_swap(cls, target_ollama_name: str, role: str) -> str:
        """
        Executes the swap physically into the environment and config states.
        Wave 9.4: engine-aware — an MLX target flips the role's engine flag to "mlx"
        (and sets the mlx_* model id); an Ollama target flips it back to "ollama". So
        selecting an MLX model in the Locker adopts MLX with NO .env editing.
        """
        # MLX target → engine=mlx swap (bypasses Ollama analyze_swap / disk math).
        if cls._is_mlx_target(target_ollama_name):
            return cls._execute_mlx_swap(target_ollama_name, role)

        is_safe, message, changes = cls.analyze_swap(target_ollama_name, role)

        if not is_safe:
            raise ModelSwapError(message)

        # Ensure the role's engine flag reflects an Ollama target (undo a prior MLX pin).
        _eng = {"main_model": "main_engine", "fast_model": "fast_engine",
                "vision_model": "vision_engine", "embedding_model": "embed_engine"}.get(role)
        if _eng:
            changes[_eng] = "ollama"
        # Option A (reverse) — switching MAIN back to Ollama returns vision to Ollama too;
        # its runtime `resolve_vision_model` then rides the (vision-capable) main model.
        if role == "main_model":
            changes["vision_engine"] = "ollama"

        # Write changes to the config environment
        cls._patch_environment(changes)

        return message

    @staticmethod
    def _is_mlx_target(name: str) -> bool:
        """True if `name` refers to an MLX model (a configured mlx_* id or a known MLX org repo)."""
        from config import settings as s
        if name in {getattr(s, "mlx_main_model", None), getattr(s, "mlx_fast_model", None),
                    getattr(s, "mlx_vision_model", None), getattr(s, "mlx_embedding_model", None)}:
            return True
        return "/" in name and any(name.startswith(o) for o in (
            "mlx-community/", "Runpod/", "lmstudio-community/", "unsloth/",
            "AITRADER/", "themindstudio/"))

    @classmethod
    def _check_mlx_swap_safe(cls, mlx_model: str, role: str) -> None:
        """Refuse a swap that cannot run on this machine. Raises ModelSwapError.

        Two failure modes, both silent before this existed:
          · the model is not on disk — the swap "succeeds" and the app then stalls on a
            multi-GB download inside the user's first request, or fails outright offline;
          · the model does not fit — on a 16 GB box that means swap-death or, at the extreme
            documented in mlx-lm#883, a GPU watchdog reboot, because wired memory blocks
            Jetsam so the driver panics instead of the process being killed.

        Deliberately permissive where the data is missing: an unknown SIZE warns rather than
        refuses. Refusing on a guess is how the old estimator's 0.00 GB for arctic would have
        blocked a model that runs fine.
        """
        try:
            from services.model_presence import is_present
            if not is_present(mlx_model):
                raise ModelSwapError(
                    f"{mlx_model} is not downloaded. Download it first — swapping now would "
                    f"stall your next request on a multi-GB download (or fail offline).")
        except ModelSwapError:
            raise
        except Exception as e:
            logger.debug(f"[locker] MLX presence check skipped: {e}")

        try:
            from services.model_sizing import fit
            f = fit(mlx_model, 16384)
            if f.get("fits") is False:
                raise ModelSwapError(
                    f"{mlx_model} needs ~{f.get('total_needed_gb')} GB (weights + KV at 16k) "
                    f"but this machine's budget is {f.get('budget_gb')} GB. "
                    f"Loading it risks swap-death on a {round(f.get('working_set_gb', 0))} GB "
                    f"working set. Choose a smaller model or quantization.")
            if f.get("recommendation") == "tight":
                logger.warning(f"[locker] {mlx_model} is a TIGHT fit "
                               f"({f.get('total_needed_gb')} of {f.get('budget_gb')} GB) — "
                               f"expect memory pressure with other models resident")
        except ModelSwapError:
            raise
        except Exception as e:
            logger.debug(f"[locker] MLX fit check skipped: {e}")

    @classmethod
    def _execute_mlx_swap(cls, mlx_model: str, role: str) -> str:
        """Flip a role to the MLX engine + set its mlx model id. Persisted + in-memory."""
        role_map = {
            "main_model": ("main_engine", "mlx_main_model"),
            "fast_model": ("fast_engine", "mlx_fast_model"),
            "vision_model": ("vision_engine", "mlx_vision_model"),
            "embedding_model": ("embed_engine", "mlx_embedding_model"),
        }
        if role not in role_map:
            raise ModelSwapError(f"MLX engine swap is not supported for role '{role}'")
        eng_attr, model_attr = role_map[role]

        # SAFETY (Stage 3.8). `execute_swap` short-circuits here BEFORE `analyze_swap`, so
        # every guardrail — RAM fit, headroom, min_ram refusal — applied only to Ollama
        # targets. MLX swaps had NONE, and deleting the Ollama half would have deleted the
        # Locker's only safety layer rather than half of it.
        cls._check_mlx_swap_safe(mlx_model, role)

        changes = {eng_attr: "mlx", model_attr: mlx_model}
        # Option A — a vision-capable MLX MAIN model absorbs the vision slot too (one gemma
        # load serves text + vision; the memory win depends on NOT loading it twice). Mirrors
        # the Ollama `resolve_vision_model` behaviour. Only when the model actually has vision.
        extra = ""
        if role == "main_model":
            try:
                from evaluator.capability_probe import probe_capabilities
                caps = probe_capabilities(mlx_model, provider="mlx")
                if caps and caps.vision:
                    changes["vision_engine"] = "mlx"
                    changes["mlx_vision_model"] = mlx_model
                    extra = " (vision follows — Option A)"
            except Exception:
                pass
        cls._patch_environment(changes)
        # When the user has gone all-MLX for the text/vision roles, bring the two remaining
        # capabilities onto MLX too and kick off their downloads in the background NOW — so the
        # first time they're used the model is already on disk instead of stalling on a lazy
        # first-use fetch mid-session (user requests 2026-07-17 image, 2026-07-22 embeddings):
        #   • image generation (klein/mflux, ~4 GB) — else "Klein model not installed"
        #   • embeddings (arctic-embed-l-v2.0 bf16, 1.1 GB measured) — else the first RAG search / @curator
        #     routing / constellation clustering / memory recall blocks on the download.
        # Embeddings run the SAME arctic model at the SAME 1024 dim as Ollama → NO re-index; both
        # hooks are fallback-safe (a failed download just falls back to the Ollama path).
        try:
            from config import settings as _s
            text_all_mlx = (getattr(_s, "main_engine", "") == "mlx"
                            and getattr(_s, "fast_engine", "") == "mlx"
                            and getattr(_s, "vision_engine", "") == "mlx")
            if text_all_mlx:
                import asyncio
                from services.mlx_download import mlx_download_manager

                def _prefetch(model_id: str) -> bool:
                    """Start a background HF download so the model is ready, not fetched mid-use.
                    No running loop (sync caller) → returns False; the model still downloads lazily."""
                    if not model_id:
                        return False
                    try:
                        asyncio.get_running_loop().create_task(mlx_download_manager.start(model_id))
                        return True
                    except RuntimeError:
                        return False

                if getattr(_s, "image_engine", "ollama") != "mlx":
                    cls._patch_environment({"image_engine": "mlx"})
                    if _prefetch(getattr(_s, "mlx_image_model", "")):
                        extra += " · image→MLX (klein downloading)"
                if getattr(_s, "embed_engine", "ollama") != "mlx":
                    cls._patch_environment({"embed_engine": "mlx"})
                    if _prefetch(getattr(_s, "mlx_embedding_model", "")):
                        extra += " · embed→MLX (arctic downloading)"
        except Exception as _e:
            logger.debug(f"[llm_locker] all-MLX prefetch hook skipped: {_e}")
        return f"Switched {role} to the MLX engine: {mlx_model}{extra}"
        
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
                "MAX_RAG_CONTEXT": "LOCALBOOK_MAX_RAG_CONTEXT",
                # Wave 9.4 — engine flags + mlx model ids use BARE field names (pydantic
                # reads these from .env by field name; LOCALBOOK_-prefixed keys are ignored).
                "main_engine": "main_engine", "fast_engine": "fast_engine",
                "vision_engine": "vision_engine", "image_engine": "image_engine",
                "embed_engine": "embed_engine",
                "mlx_main_model": "mlx_main_model", "mlx_fast_model": "mlx_fast_model",
                "mlx_vision_model": "mlx_vision_model", "mlx_embedding_model": "mlx_embedding_model",
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
        # Wave 9.4 — sync engine flags + mlx model ids to the live settings (session-immediate).
        for _attr in ("main_engine", "fast_engine", "vision_engine", "image_engine", "embed_engine",
                      "mlx_main_model", "mlx_fast_model", "mlx_vision_model", "mlx_embedding_model"):
            if _attr in changes:
                setattr(settings, _attr, changes[_attr])
            
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
