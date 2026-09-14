"""Model discovery without Ollama, and the profiles that must survive the switch.

Stage 3.3/3.4/3.6 — three of the four blocker-class cutover gaps:

  · `GET /evaluator/models` was empty with Ollama gone, because presence came only from
    `/api/tags`. MLX models live in the HF cache, so presence is a filesystem question.
  · `GET /settings/ollama/models` raised 503 when Ollama was unreachable — killing the endpoint
    BEFORE its own MLX block, so the Locker showed nothing INCLUDING MLX cards.
  · The registry had no MLX rows, so the curated per-model profiles (num_ctx_cap,
    repeat_penalty, stop sequences) went inert the moment a role held an HF id. Seven consumers
    read them; none would have errored, they would just have stopped applying.
"""
import pytest

from services import model_presence as mp


def test_enumerate_finds_real_models_with_sizes():
    models = mp.enumerate_cached(force=True)
    assert models, "no models found in the HF cache"
    assert all(m["weight_gb"] > 0 for m in models), "a model with no weights is not runnable"
    ids = {m["model_id"] for m in models}
    assert any("gemma" in i for i in ids)


def test_enumerate_excludes_repos_with_no_weights():
    """A cache entry can exist for a tokenizer or a dataset. Listing those as installable
    models would be a lie the Locker cannot act on."""
    for m in mp.enumerate_cached():
        assert m["weight_gb"] is not None and m["weight_gb"] > 0


def test_is_present_is_a_filesystem_fact_not_a_network_call():
    assert mp.is_present("mlx-community/gemma-4-e4b-it-4bit") is True
    assert mp.is_present("mlx-community/definitely-not-downloaded") is False
    assert mp.is_present("") is False


def test_vision_and_context_are_read_from_the_checkpoint():
    by_id = {m["model_id"]: m for m in mp.enumerate_cached()}
    gemma = by_id.get("mlx-community/gemma-4-e4b-it-4bit")
    assert gemma and gemma["vision"] is True
    assert gemma["native_ctx"] == 131072
    phi = by_id.get("mlx-community/Phi-4-mini-instruct-4bit")
    assert phi and phi["vision"] is False


def test_readiness_names_the_blocking_roles():
    r = mp.readiness({"main": "mlx-community/gemma-4-e4b-it-4bit",
                      "fast": "mlx-community/not-downloaded",
                      "embed": "mlx-community/snowflake-arctic-embed-l-v2.0-bf16"})
    assert r["blocking"] == ["fast"]
    assert r["ready"] is False


def test_vision_is_not_blocking():
    """The app degrades to text-only rather than failing, so a missing vision model must not
    report the whole app as un-ready."""
    r = mp.readiness({"main": "mlx-community/gemma-4-e4b-it-4bit",
                      "fast": "mlx-community/Phi-4-mini-instruct-4bit",
                      "embed": "mlx-community/snowflake-arctic-embed-l-v2.0-bf16",
                      "vision": "mlx-community/not-downloaded"})
    assert r["blocking"] == []
    assert r["ready"] is True


def test_mlx_models_resolve_the_same_curated_profile_as_their_ollama_twin():
    """THE regression this guards: the profiles are keyed by model name, so an MLX id with no
    registry row silently loses num_ctx_cap / repeat_penalty / stop sequences. No error — the
    tuning just stops applying, which is the hardest kind of regression to notice."""
    from services.llm_service import _get_rag_profile

    for ollama_name, mlx_id in (
        ("gemma4:e4b", "mlx-community/gemma-4-e4b-it-4bit"),
        ("phi4-mini:latest", "mlx-community/Phi-4-mini-instruct-4bit"),
    ):
        a, b = _get_rag_profile(ollama_name), _get_rag_profile(mlx_id)
        assert a, f"{ollama_name} has no profile — the fixture is wrong"
        assert b, f"{mlx_id} resolved NO profile — curated tuning is inert on MLX"
        assert a == b, f"{mlx_id} profile diverged from its Ollama twin"


def test_registry_reads_the_configured_ollama_url():
    """`from backend.config import get_settings` was a broken import that raised on every call
    and silently fell back to a hardcoded localhost:11434, ignoring a configured port."""
    import inspect

    from evaluator import model_registry as mr

    src = inspect.getsource(mr.ModelRegistry.refresh_installed_status)
    # Strip comments — the fix is DESCRIBED in a comment, so a naive substring search matches
    # the explanation rather than the code. (Caught by this test failing on its first run.)
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "from backend.config import get_settings" not in code
    assert "from config import settings" in code


# ── Diffusion layouts (2026-08-20) ──────────────────────────────────────────────

def test_a_diffusion_checkpoint_reports_its_real_weight():
    """THE bug this guards. `exact_weight_gb` read `model.safetensors.index.json` at the
    snapshot ROOT and, failing that, summed root-level `*.safetensors`. FLUX/Klein has
    neither: it is several components — `transformer/`, `text_encoder/`, `vae/` — each with
    its own index. Both paths found zero bytes and returned None, which `is_present` reads as
    "not downloaded".

    Consequences of the false negative, all observed: a fully-downloaded 4.3 GB Klein was
    reported absent, `enumerate_cached` skipped it so it never appeared in the model browser,
    the prefs migration refused to promote the image role, and I told the user image
    generation was blocked on a download that had already happened.
    """
    from services.model_sizing import exact_weight_gb

    gb = exact_weight_gb("Runpod/FLUX.2-klein-4B-mflux-4bit")
    assert gb is not None, "nested-component weights must be found"
    assert gb > 3.0, f"expected the real multi-GB size, got {gb}"
    assert mp.is_present("Runpod/FLUX.2-klein-4B-mflux-4bit") is True


def test_the_model_browser_lists_the_diffusion_model():
    """`enumerate_cached` drops anything with no weights, so the sizing bug hid Klein from
    every UI that enumerates installable models."""
    ids = {m["model_id"] for m in mp.enumerate_cached(force=True)}
    assert "Runpod/FLUX.2-klein-4B-mflux-4bit" in ids


def test_sizing_still_works_for_ordinary_sharded_checkpoints():
    """The recursive walk must not regress the common case."""
    from services.model_sizing import exact_weight_gb

    gemma = exact_weight_gb("mlx-community/gemma-4-e4b-it-4bit")
    assert gemma is not None and 4.0 < gemma < 6.0, gemma


def test_non_safetensors_weight_formats_are_counted():
    """mlx-whisper ships a single `weights.npz`. Counting only safetensors reported it as
    absent — the same false negative as Klein, a different cause. A model browser that
    enumerates the cache has to see every format we actually ship."""
    from services.model_sizing import exact_weight_gb

    assert exact_weight_gb("mlx-community/whisper-base-mlx") is not None


def test_every_cached_model_reports_a_size():
    """Sweep, not a spot check: any cached repo we cannot size is invisible to presence,
    readiness and the model browser."""
    from huggingface_hub import scan_cache_dir
    from services.model_sizing import exact_weight_gb

    invisible = [
        r.repo_id for r in scan_cache_dir().repos
        if getattr(r, "repo_type", "model") == "model"
        and r.size_on_disk > 50 * 1024 ** 2      # ignore tokenizer-only stubs
        and exact_weight_gb(r.repo_id) is None
    ]
    assert not invisible, f"cached but unsizeable: {invisible}"


# ── Offline model loading (2026-08-20) ──────────────────────────────────────────

def test_loading_a_cached_model_never_contacts_the_hub():
    """`mlx_lm.load` / `mlx_vlm.get_model_path` / `hf_hub_download` all REVALIDATE against
    huggingface.co even when the file is already cached. Three consequences, all observed in
    backend.log: an "unauthenticated requests to the HF Hub" warning on every cold start,
    model loading that depends on network reachability, and a request leaving a machine whose
    whole premise is that nothing does.

    `offline_if_cached` engages only when the weights are already on disk, so a genuine first
    download still works.
    """
    import os

    from services.mlx_engine import offline_if_cached

    present = "mlx-community/gemma-4-e4b-it-4bit"
    absent = "mlx-community/definitely-not-downloaded"

    with offline_if_cached(present):
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        import huggingface_hub.constants as hc
        assert hc.HF_HUB_OFFLINE is True, "the constant is captured at import; the env var alone is too late"
    assert os.environ.get("HF_HUB_OFFLINE") is None, "prior state must be restored"

    with offline_if_cached(absent):
        assert os.environ.get("HF_HUB_OFFLINE") is None, \
            "an uncached model must stay online so a first download can proceed"


def test_model_kind_detection_reads_the_cache_not_the_hub():
    """`_model_kind` ran BEFORE the load lock and called `hf_hub_download`, so it sat outside
    `offline_if_cached` — it, not the load itself, emitted the warning on every cold start."""
    import inspect

    from services import mlx_engine as me

    src = inspect.getsource(me.MLXEngine._model_kind)
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "hf_hub_download" not in code
    assert me.mlx_engine._model_kind("mlx-community/gemma-4-e4b-it-4bit") == "vlm"
    assert me.mlx_engine._model_kind("mlx-community/Phi-4-mini-instruct-4bit") == "lm"
