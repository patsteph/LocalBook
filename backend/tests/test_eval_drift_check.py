"""The end-of-run model-drift check must not crash the run, or invent drift.

Two real bugs lived here, both introduced 2026-08-19 while making the combo snapshot
engine-aware, and both only reachable at the very END of a full evaluation — i.e. after
20+ minutes of work:

1. `NameError: name '_mr' is not defined` — the import was removed with the block that
   used to need it. It crashed a real MLX eval at the finish line (user report).
2. Phantom drift on every MLX run: the snapshot holds RESOLVED values (`mlx-community/...`)
   while the check compared them against raw `settings.ollama_model` (`gemma4:e4b`). Those
   can never be equal, so every MLX run would have ended with a false "model swap detected"
   warning.

The lesson worth pinning: a failure that only surfaces at the end of a long run is
disproportionately expensive, so it needs a cheap test.
"""
import pytest

from config import settings
from evaluator.models import ModelCombo


@pytest.fixture
def restore_engines():
    keep = {k: getattr(settings, k, "ollama")
            for k in ("main_engine", "fast_engine", "vision_engine", "embed_engine")}
    yield
    for k, v in keep.items():
        setattr(settings, k, v)


def _snapshot():
    """Exactly what run_full_evaluation records at start."""
    c = ModelCombo.from_config(settings)
    return {
        "ollama_model": c.main_model,
        "ollama_fast_model": c.fast_model,
        "vision_model": c.vision_model,
        "embedding_model": c.embedding_model,
        "main_engine": c.main_engine,
        "fast_engine": c.fast_engine,
        "vision_engine": c.vision_engine,
        "embed_engine": c.embed_engine,
    }


def _drift(snapshot):
    """The comparison the service performs at the end of a run."""
    now = _snapshot()
    return [f"{k}: '{v}' -> '{now.get(k, '')}'" for k, v in snapshot.items() if now.get(k, "") != v]


def test_no_drift_when_nothing_changed_on_ollama(restore_engines):
    settings.main_engine = settings.fast_engine = "ollama"
    assert _drift(_snapshot()) == []


def test_no_phantom_drift_on_an_all_mlx_run(restore_engines):
    """THE regression: comparing a resolved HF id against settings.ollama_model would report
    drift on a run where nothing changed at all."""
    settings.main_engine = settings.fast_engine = "mlx"
    settings.vision_engine = settings.embed_engine = "mlx"
    assert _drift(_snapshot()) == []


def test_real_drift_is_still_detected(restore_engines):
    """The check must keep doing its job — a mid-run engine swap makes the report a mix of two
    configurations and has to be surfaced."""
    settings.main_engine = "ollama"
    snap = _snapshot()
    settings.main_engine = "mlx"          # the user swaps mid-run
    drifted = _drift(snap)
    assert any("main_engine" in d for d in drifted), drifted
    assert any("ollama_model" in d for d in drifted), "the resolved model changed too"


def test_the_service_defines_every_name_the_drift_check_uses():
    """`_mr` was undefined here and only blew up at the end of a 20-minute run. A cheap
    import + name check catches that class of bug in milliseconds."""
    import ast
    import inspect
    import evaluator.evaluator_service as svc

    src = inspect.getsource(svc)
    tree = ast.parse(src)
    module_names = set(dir(svc))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "run_full_evaluation":
            continue
        bound = set(module_names)
        for n in ast.walk(node):
            if isinstance(n, ast.Import):
                bound |= {a.asname or a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                bound |= {a.asname or a.name for a in n.names}
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                bound.add(n.id)
            elif isinstance(n, ast.arg):
                bound.add(n.arg)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)          # `except X as e:` binds e
            elif isinstance(n, (ast.comprehension,)):
                for t in ast.walk(n.target):
                    if isinstance(t, ast.Name):
                        bound.add(t.id)
        used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        import builtins
        unknown = used - bound - set(dir(builtins))
        assert not unknown, f"run_full_evaluation references undefined name(s): {sorted(unknown)}"
