"""user_preferences.json schema v2 — make the saved combo survive the MLX cutover.

Stage 3.5, and the most dangerous migration in the cutover because it rewrites USER data on
every existing install.

THE PROBLEM. `main.py:103-110` restores `default_combo` over the config defaults on every
launch, with a truthy check — so a saved value always wins. Today that file holds, per role,
BOTH an engine flag (`main_engine: "ollama"`) and two model names (`ollama_model`,
`mlx_main_model`). After the excise the engine flags are meaningless and the Ollama names can
no longer be loaded by anything, but the restore loop would keep writing them into role slots
that only accept HF ids. Verified live: this machine's file says `"ollama"` for all five roles
while `config.py` defaults to `mlx` for embed — the file silently wins.

WHAT THIS DOES. Adds `schema_version: 2` and, for each role, records the RESOLVED model — the
one that engine flag actually selects — under a `resolved` block, while leaving every existing
key untouched. That makes the file forward-compatible without breaking the current reader:
v1 keys still work today, and the excise can later read `resolved` and drop the rest.

DELIBERATELY CONSERVATIVE:
  · **Backs up first**, timestamped. Three lines, and the difference between a revert and
    reconstructing five role slots by hand across three machines.
  · **Never deletes a key.** A migration that removes data cannot be undone by re-running it.
  · **Idempotent** — marked by `schema_version`, so a second run is a no-op.
  · **Never raises.** A prefs migration that stops the app booting is worse than stale prefs.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4
# v2 = resolved_roles recorded. v3 = engine promotion to MLX. v4 = the ROLE COLLAPSE: each role
# is now ONE key holding a checkpoint id, so `mlx_main_model` folds into `main_model` and the
# `*_engine` flags are dropped. A file at any earlier version still stores the old pairs, and
# `main.py`'s restore loop would write an Ollama NAME into a role that can only load an MLX id —
# which is why the bump matters rather than being cosmetic.

# role → (engine key, legacy-name key, mlx-id key) AS STORED IN THE FILE.
#
# ⚠️ These are the COMBO's key names, which are NOT the settings attribute names — the file
# stores `embeddings`, not `embedding_model`. Reading the settings names instead silently
# resolved three roles to "" (caught by running the migration against a COPY of the real file
# rather than trusting the mapping).
#
# ⚠️⚠️ These strings describe DATA ON DISK written by older versions. They must NOT be renamed
# to track config.py — a bulk rename during the v2.3.0 collapse rewrote the `mlx_*` column to
# match the new attribute names, which made both columns identical, so the promotion read the
# Ollama name and "promoted" every role to a model MLX cannot load. Concatenated below so a
# careless search-and-replace cannot silently do it again.
_MLX = "mlx_"
ROLES = {
    "main":   ("main_engine",   "main_model",  _MLX + "main_model"),
    "fast":   ("fast_engine",   "fast_model",  _MLX + "fast_model"),
    "vision": ("vision_engine", "vision_model", _MLX + "vision_model"),
    "embed":  ("embed_engine",  "embeddings",  _MLX + "embedding_model"),
    "image":  ("image_engine",  "image_model", _MLX + "image_model"),
}


def _prefs_path() -> Optional[str]:
    try:
        from config import settings
        return os.path.join(str(settings.data_dir), "user_preferences.json")
    except Exception:
        return None


def run(path: Optional[str] = None) -> Dict[str, Any]:
    """Migrate the prefs file to schema v2. Returns a summary; never raises."""
    out: Dict[str, Any] = {"ran": False, "reason": "", "backup": None, "resolved": {}}
    p = path or _prefs_path()
    if not p or not os.path.isfile(p):
        out["reason"] = "no prefs file (fresh install)"
        return out

    try:
        with open(p) as fh:
            data = json.load(fh)
    except Exception as e:
        # A corrupt prefs file is a real possibility and NOT ours to fix destructively.
        out["reason"] = f"unreadable ({type(e).__name__}) — left untouched"
        logger.warning(f"[migrate-prefs] {out['reason']}")
        return out

    if data.get("schema_version", 1) >= SCHEMA_VERSION:
        out["reason"] = f"already v{SCHEMA_VERSION}"
        return out

    combo = data.get("default_combo") or {}
    if not combo:
        # Nothing to resolve, but still stamp the version so we don't re-check every boot.
        data["schema_version"] = SCHEMA_VERSION
        _write(p, data, out)
        out["reason"] = "no default_combo — stamped only"
        return out

    resolved: Dict[str, Dict[str, Any]] = {}
    for role, (eng_key, ollama_key, mlx_key) in ROLES.items():
        engine = combo.get(eng_key) or "ollama"
        model = combo.get(mlx_key) if engine == "mlx" else combo.get(ollama_key)
        resolved[role] = {"engine": engine, "model": model or ""}

    data["schema_version"] = SCHEMA_VERSION
    # Additive: the v1 reader ignores this, the post-excise reader uses it.
    data["resolved_roles"] = resolved

    # ── Stage 4 Phase 1: adopt the new MLX defaults for roles the user never chose. ──
    # `main.py`'s restore loop applies default_combo over config.py with a truthy check, so a
    # saved "ollama" beats the new default on EVERY launch and the flip would never take effect
    # on an existing install. Verified live: this machine's file pinned all five roles to
    # "ollama" while config already defaulted embed to mlx — the file silently won.
    #
    # Only rewrites a role whose saved engine is "ollama" AND whose MLX model is present on
    # disk. A deliberate Ollama choice on a machine without the MLX weights is left alone —
    # silently repointing a role at a model that is not there is how a first run stalls.
    # `image` is included: Klein IS an MLX model and is auto-downloaded when a machine adopts
    # all-MLX. It was excluded here only because `is_present` wrongly reported it absent —
    # `exact_weight_gb` looked for weights at the snapshot ROOT, and diffusion checkpoints
    # keep theirs in transformer/ text_encoder/ vae/. The presence gate below is the real
    # protection, so no role needs a hardcoded exception.
    promoted = {}
    try:
        from services.model_presence import is_present
        for role, (eng_key, _ok, mlx_key) in ROLES.items():
            if combo.get(eng_key) != "ollama":
                continue
            mlx_model = combo.get(mlx_key)
            if mlx_model and is_present(mlx_model):
                combo[eng_key] = "mlx"
                promoted[role] = mlx_model
        if promoted:
            data["default_combo"] = combo
            data["resolved_roles"] = {
                r: ({"engine": "mlx", "model": promoted[r]} if r in promoted else v)
                for r, v in resolved.items()
            }
    except Exception as e:
        logger.warning(f"[migrate-prefs] engine promotion skipped: {e}")
    out["promoted"] = promoted

    # ── v4: collapse each role pair into one key ────────────────────────────────
    # `mlx_main_model` → `main_model`, and so on. The MLX id wins because it is the only
    # thing that can actually be loaded. Engine flags are dropped; they selected between two
    # halves of a pair that no longer exists.
    _COLLAPSE = [(_MLX + "main_model", "main_model"),
                 (_MLX + "fast_model", "fast_model"),
                 (_MLX + "vision_model", "vision_model"),
                 (_MLX + "image_model", "image_model"),
                 (_MLX + "embedding_model", "embeddings")]
    collapsed = {}
    for mlx_key, role_key in _COLLAPSE:
        mlx_id = combo.get(mlx_key)
        if mlx_id:
            combo[role_key] = mlx_id
            collapsed[role_key] = mlx_id
        combo.pop(mlx_key, None)
    for _eng, _, _ in ROLES.values():
        combo.pop(_eng, None)

    # `embeddings` is the combo's historical name for the embedding role; the settings
    # attribute is `embedding_model`. Write both so a reader of either shape works.
    if combo.get("embeddings"):
        combo["embedding_model"] = combo["embeddings"]

    data["default_combo"] = combo
    out["collapsed"] = collapsed
    data["resolved_roles"] = {
        r: {"engine": "mlx", "model": combo.get(k, "")}
        for r, k in (("main", "main_model"), ("fast", "fast_model"),
                     ("vision", "vision_model"), ("embed", "embedding_model"),
                     ("image", "image_model"))
    }

    if not _write(p, data, out):
        return out

    out["ran"] = True
    out["resolved"] = {r: v["model"] for r, v in resolved.items()}
    logger.info(f"[migrate-prefs] v2: resolved roles {out['resolved']}")
    return out


def _write(path: str, data: Dict[str, Any], out: Dict[str, Any]) -> bool:
    """Back up, then write atomically. Returns False (and leaves the file alone) on failure."""
    try:
        backup = f"{path}.pre-v{SCHEMA_VERSION}-backup-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
        out["backup"] = backup
    except Exception as e:
        out["reason"] = f"backup failed ({type(e).__name__}) — NOT migrating"
        logger.warning(f"[migrate-prefs] {out['reason']}")
        return False
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)          # atomic — never a half-written prefs file
        return True
    except Exception as e:
        out["reason"] = f"write failed ({type(e).__name__})"
        logger.warning(f"[migrate-prefs] {out['reason']}")
        return False
