"""Model Registry — catalog of known Ollama models with vendor/origin metadata.

Loads from registry_data/known_models.json and augments with live Ollama API data.
Supports policy filtering (e.g., exclude models from specific countries).
"""

import json
import subprocess
from pathlib import Path
from typing import Optional
from evaluator.models import ModelInfo
import logging
logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).parent / "registry_data" / "known_models.json"


class ModelRegistry:
    """Catalog of known models with vendor metadata + installed model detection."""

    def __init__(self):
        self._models: dict[str, ModelInfo] = {}
        self._loaded = False

    def load(self):
        """Load the seed registry from known_models.json."""
        if self._loaded:
            return
        try:
            data = json.loads(_REGISTRY_PATH.read_text())
            for entry in data.get("models", []):
                info = ModelInfo(
                    ollama_name=entry.get("ollama_name", ""),
                    display_name=entry.get("display_name", ""),
                    family=entry.get("family", ""),
                    parameter_count=entry.get("parameter_count", ""),
                    vendor=entry.get("vendor", ""),
                    origin_country=entry.get("origin_country", ""),
                    license=entry.get("license", ""),
                    supported_roles=entry.get("supported_roles", []),
                    context_window=entry.get("context_window", 4096),
                    supports_json_mode=entry.get("supports_json_mode", False),
                    supports_vision=entry.get("supports_vision", False),
                    vision_api_style=entry.get("vision_api_style", "generate"),
                    embedding_dim=entry.get("embedding_dim", 0),
                    disk_size_gb=entry.get("disk_size_gb", 0.0),
                    min_ram_gb=entry.get("min_ram_gb", 0),
                    recommended_ram_gb=entry.get("recommended_ram_gb", 0),
                    policy_tags=entry.get("policy_tags", []),
                    ollama_options=entry.get("ollama_options", {}),
                    rag_profile=entry.get("rag_profile", {}),
                    vision_profile=entry.get("vision_profile", {}),
                    audio_profile=entry.get("audio_profile", {}),
                    structured_profile=entry.get("structured_profile", {}),
                    provider=entry.get("provider", "ollama"),
                )
                self._models[info.ollama_name] = info
            self._loaded = True
            print(f"[MODEL-REGISTRY] Loaded {len(self._models)} known models")
        except FileNotFoundError:
            print(f"[MODEL-REGISTRY] Registry file not found: {_REGISTRY_PATH}")
            self._loaded = True
        except Exception as e:
            print(f"[MODEL-REGISTRY] Failed to load registry: {e}")
            self._loaded = True

    def get_model(self, ollama_name: str) -> Optional[ModelInfo]:
        """Get info for a specific model. Returns None if unknown."""
        self.load()
        # Try exact match first
        if ollama_name in self._models:
            return self._models[ollama_name]
        # Try without tag (e.g., "olmo-3" matches "olmo-3:7b-instruct")
        for key, info in self._models.items():
            if key.startswith(ollama_name.split(":")[0]):
                return info
        return None

    def resolve_vision_model(self, main_model: str, configured: str) -> str:
        """Pick the vision model — gemma4 migration "Option A".

        Precedence: an explicit ``LOCALBOOK_VISION_MODEL`` env override wins;
        else a vision-capable MAIN model absorbs the vision slot (it's already
        resident — no extra load, and no dependency on a separately-installed
        model like granite, which HTTP-404s on machines that don't have it);
        else fall back to the ``configured`` vision model (Option B).
        """
        import os
        env = os.getenv("LOCALBOOK_VISION_MODEL")
        if env:
            return env
        info = self.get_model(main_model)
        if info and info.supports_vision:
            return main_model
        return configured

    def list_all(self) -> list[ModelInfo]:
        """List all known models, refreshed with local install status."""
        self.load()
        self.refresh_installed_status()
        return list(self._models.values())

    def list_for_role(self, role: str, policy_filter: Optional[dict] = None) -> list[ModelInfo]:
        """List models valid for a specific role, refreshed with local install status."""
        self.load()
        self.refresh_installed_status()
        results = [m for m in self._models.values() if role in m.supported_roles]
        if policy_filter:
            results = self._apply_policy(results, policy_filter)
        return results

    def filter_by_policy(
        self,
        exclude_origins: list[str] = None,
        exclude_vendors: list[str] = None,
    ) -> list[ModelInfo]:
        """Filter all models by policy constraints."""
        self.load()
        models = list(self._models.values())
        return self._apply_policy(models, {
            "exclude_origins": exclude_origins or [],
            "exclude_vendors": exclude_vendors or [],
        })

    def _apply_policy(self, models: list[ModelInfo], policy: dict) -> list[ModelInfo]:
        """Apply policy filter to model list."""
        exclude_origins = [o.upper() for o in policy.get("exclude_origins", [])]
        exclude_vendors = [v.lower() for v in policy.get("exclude_vendors", [])]

        filtered = []
        for m in models:
            if m.origin_country.upper() in exclude_origins:
                continue
            if m.vendor.lower() in exclude_vendors:
                continue
            filtered.append(m)
        return filtered

    def refresh_installed_status(self):
        """Refresh is_installed for every registry entry.

        - Ollama-provider models: presence in GET /api/tags.
        - llama-server-provider models: sidecar GET /health returns 200.
          (llama-server loads exactly one model at boot, so if it's healthy we
          treat the registered model as installed.)
        """
        # ── 1. Ollama models ──
        installed_ollama: set[str] = set()
        try:
            import urllib.request
            import json
            # Fallback to default port if not in config
            base_url = "http://localhost:11434"
            try:
                from backend.config import get_settings
                base_url = get_settings().ollama_base_url
            except Exception as _e:
                logger.debug(f"[model-registry] {type(_e).__name__}: {_e}")

            req = urllib.request.Request(f"{base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5.0) as response:
                if response.getcode() == 200:
                    data = json.loads(response.read().decode())
                    installed_ollama = {m.get("name") for m in data.get("models", []) if "name" in m}
                else:
                    print(f"[MODEL-REGISTRY] Ollama /api/tags returned {response.getcode()}")
        except Exception as e:
            print(f"[MODEL-REGISTRY] Failed to refresh Ollama install status: {e}")

        # ── 2. llama-server sidecar health (cheap, cached) ──
        sidecar_healthy = False
        try:
            from services.llm_provider import health_check_sync, Provider
            sidecar_healthy = health_check_sync(Provider.LLAMA_SERVER)
        except Exception as _e:
            logger.debug(f"[model-registry] sidecar health check failed: {_e}")

        # ── 3. Update flags per-entry based on provider ──
        for name, model in self._models.items():
            if getattr(model, "provider", "ollama") == "llama_server":
                model.is_installed = sidecar_healthy
                continue
            # Exact match or tag-less match (e.g. "llama3" matches "llama3:latest")
            model.is_installed = (name in installed_ollama)
            if not model.is_installed:
                base_name = name.split(":")[0]
                model.is_installed = any(n.startswith(base_name) for n in installed_ollama)

    def get_installed_models(self) -> list[dict]:
        """Query Ollama for currently installed models (Legacy/Direct)."""
        self.refresh_installed_status()
        models = []
        for name, model in self._models.items():
            if model.is_installed:
                models.append(model.to_dict())
        return models


# Singleton
model_registry = ModelRegistry()
