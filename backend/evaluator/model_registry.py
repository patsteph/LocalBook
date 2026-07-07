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
        """Get info for a specific model. Returns None if unknown.

        A TAGGED query with no exact match is a DISTINCT model — e.g.
        ``gemma4:12b`` must NOT fall back to the registered ``gemma4:e4b``
        entry (different size/params). Only a TAG-LESS query (e.g. ``olmo-3``)
        loose-matches a registered tag of that exact base family.
        """
        self.load()
        # Exact match first
        if ollama_name in self._models:
            return self._models[ollama_name]
        # Tag-less query → match a registered tag of that exact base family.
        if ":" not in ollama_name:
            for key, info in self._models.items():
                if key.split(":")[0] == ollama_name:
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
        self._installed_tags = []  # full /api/tags entries (name, size, details) for live cards
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
                    self._installed_tags = data.get("models", []) or []
                    installed_ollama = {m.get("name") for m in self._installed_tags if m.get("name")}
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
            # Exact tag match, or ":latest" equivalence for a tag-less registry
            # name. A DIFFERENT explicit tag is a different model — gemma4:12b
            # must NOT mark gemma4:e4b installed.
            model.is_installed = (
                name in installed_ollama
                or (":" not in name and f"{name}:latest" in installed_ollama)
                or (name.endswith(":latest") and name[: -len(":latest")] in installed_ollama)
            )

    def get_installed_models(self) -> list[dict]:
        """Live installed models for the evaluator cards.

        Enumerates the actual Ollama tags. A tag with an EXACT registry entry
        uses that curated card; a tag with no registry entry (e.g. a freshly
        pulled gemma4:12b) gets a card built from its OWN live Ollama metadata
        — never a sibling registry entry's name/params. llama-server sidecar
        models (no Ollama tag) are appended from the registry.
        """
        self.refresh_installed_status()
        out: list[dict] = []
        seen: set[str] = set()
        for tag in (self._installed_tags or []):
            name = tag.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            entry = self._models.get(name)  # EXACT match only
            out.append(entry.to_dict() if entry else self._live_card(tag))
        # Sidecar (llama_server) entries have no Ollama tag — include if installed.
        for name, model in self._models.items():
            if name in seen:
                continue
            if getattr(model, "provider", "ollama") == "llama_server" and model.is_installed:
                out.append(model.to_dict())
        return out

    def _live_card(self, tag: dict) -> dict:
        """Build a card from live Ollama metadata for a model that isn't in the
        curated registry — uses the model's REAL name/family/params, and (build A,
        2026-07-07) its REAL capabilities + role eligibility by PROBING the engine
        (/api/show `capabilities`) instead of defaulting to text-only/no-vision/
        no-roles. This is what stops 5 fresh models all landing in Main and a
        Qwen-VL from being told to install granite."""
        name = tag.get("name", "")
        details = tag.get("details") or {}
        size_bytes = tag.get("size") or 0

        supports_vision = False
        supported_roles: list = []
        context_window = 0
        embedding_dim = 0
        param_size = details.get("parameter_size", "") or ""
        try:
            from evaluator.capability_probe import probe_capabilities
            caps = probe_capabilities(name)
            if caps is not None:
                supports_vision = caps.vision
                supported_roles = caps.roles()
                context_window = caps.native_ctx
                embedding_dim = caps.embedding_dim
                param_size = caps.param_size or param_size
        except Exception as _e:
            logger.debug(f"[model-registry] live capability probe failed for {name}: {_e}")

        return {
            "ollama_name": name,
            "display_name": name,
            "family": details.get("family", "") or "",
            "parameter_count": param_size,
            "vendor": "",
            "origin_country": "",
            "license": "",
            "supported_roles": supported_roles,
            "context_window": context_window,
            "supports_vision": supports_vision,
            "embedding_dim": embedding_dim,
            "disk_size_gb": round(size_bytes / 1e9, 1) if size_bytes else 0.0,
            "min_ram_gb": 0,
            "policy_tags": ["uncurated"],
            "is_installed": True,
            "ollama_options": {},
            "provider": "ollama",
        }


# Singleton
model_registry = ModelRegistry()
