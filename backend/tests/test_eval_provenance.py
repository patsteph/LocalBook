"""An eval result must record the engine that actually produced it.

Test runners pass the OLLAMA model name to `stamp_provider` because that is the key the
`llm_service` seam routes on — correct for invocation, wrong for provenance. On an all-MLX run
every persisted result was therefore stamped `provider="ollama"` with an Ollama model name.

That is not cosmetic: the planned MLX-vs-Ollama A/B compares two persisted runs. With both runs
labelled "ollama" the comparison is meaningless, and — worse — it looks like it worked.
"""
import pytest

from config import settings
from evaluator.models import EvalResult


@pytest.fixture
def restore_engines():
    keep = {k: getattr(settings, k, "ollama")
            for k in ("main_engine", "fast_engine", "vision_engine", "embed_engine")}
    yield
    for k, v in keep.items():
        setattr(settings, k, v)


def _stamp(name):
    r = EvalResult(test_id="t", category="c", passed=True)
    r.stamp_provider(name)
    return r


def test_ollama_role_stamps_ollama(restore_engines):
    settings.main_engine = "ollama"
    r = _stamp(settings.ollama_model)
    assert r.provider == "ollama"
    assert r.model_used == settings.ollama_model


def test_mlx_main_role_stamps_the_mlx_id(restore_engines):
    settings.main_engine = "mlx"
    r = _stamp(settings.ollama_model)
    assert r.provider == "mlx"
    assert r.model_used == settings.mlx_main_model
    assert "/" in r.model_used, "an MLX id is an HF org/repo path"


def test_mlx_fast_role_stamps_the_mlx_id(restore_engines):
    settings.fast_engine = "mlx"
    r = _stamp(settings.ollama_fast_model)
    assert r.provider == "mlx"
    assert r.model_used == settings.mlx_fast_model


def test_mlx_embed_role_stamps_the_mlx_id(restore_engines):
    """The embed role has no `mlx_model_for_role` entry — it is not an llm_service role — so it
    needs its own resolution or it silently keeps reporting the Ollama arctic name."""
    settings.embed_engine = "mlx"
    r = _stamp(settings.embedding_model)
    assert r.provider == "mlx"
    assert r.model_used == settings.mlx_embedding_model


def test_roles_are_resolved_independently(restore_engines):
    """A half-migrated machine is the normal case during a cutover, and the most likely place
    for a mislabel to slip through."""
    settings.main_engine = "mlx"
    settings.fast_engine = "ollama"
    assert _stamp(settings.ollama_model).provider == "mlx"
    assert _stamp(settings.ollama_fast_model).provider == "ollama"


def test_an_explicit_mlx_id_still_stamps_mlx(restore_engines):
    """Runners that already hold an HF id (vision, embeddings) must keep working."""
    r = _stamp("mlx-community/some-model-4bit")
    assert r.provider == "mlx"
    assert r.backend_url == "in-process"
