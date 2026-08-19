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
