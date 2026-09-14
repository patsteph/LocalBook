"""An eval result must record the engine and model that actually produced it.

ORIGINAL BUG (2026-08-19): runners passed the OLLAMA model name to `stamp_provider` because
that was the key the `llm_service` seam routed on — correct for invocation, wrong for
provenance. Every result on an all-MLX run was stamped `provider="ollama"` with an Ollama
model name, which would have made the planned A/B compare two runs both labelled "ollama"
and look like it worked.

The v2.3.0 role collapse removed the two-names-per-role arrangement these tests were written
around: `settings.main_model` now holds the checkpoint id directly and there are no `*_engine`
flags to toggle. What still has to hold — and is what the bug was actually about — is that a
persisted result names the real model and the real engine.
"""
import pytest

from config import settings
from evaluator.models import EvalResult


def _stamp(name):
    r = EvalResult(test_id="t", category="c", passed=True)
    r.stamp_provider(name)
    return r


@pytest.mark.parametrize("attr", ["main_model", "fast_model", "vision_model", "embedding_model"])
def test_every_role_stamps_its_real_checkpoint(attr):
    """The embed role is the one that used to slip through: it is not an `llm_service` role,
    so it had no entry in the old role-mapping and kept reporting the Ollama arctic name."""
    model = getattr(settings, attr)
    r = _stamp(model)
    assert r.model_used == model
    assert r.provider == "mlx", f"{attr} stamped {r.provider}"
    assert "/" in r.model_used, "an MLX checkpoint id is an HF org/repo path"


def test_the_backend_url_says_in_process():
    """There is no server to name. A URL here would imply an HTTP hop that no longer exists."""
    assert _stamp(settings.main_model).backend_url == "in-process"


def test_a_legacy_ollama_name_is_not_relabelled_as_mlx():
    """Historical runs hold Ollama names. Stamping those "mlx" would rewrite history — the
    Eval History view has to be able to show what really produced an old result."""
    r = _stamp("gemma4:e4b")
    assert r.provider == "ollama", r.provider


def test_stamping_never_raises_on_an_unknown_model():
    """Telemetry must not be able to fail a run."""
    r = _stamp("some/model-nobody-has-heard-of")
    assert r.model_used
