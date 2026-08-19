"""MLX imports must stay FUNCTION-LOCAL — an unenforced invariant we nearly broke.

mlx-lm#1256 (OPEN, on our pinned 0.31.3): `generation_stream` is bound at module scope to
whichever thread first imports `mlx_lm.generate`, and KV arrays inherit that thread affinity
irrecoverably. LocalBook survives only because every `mlx_*` import in `backend/` happens
inside a function, so the binding lands on the single `MLXEngine._exec` thread.

Nothing enforced that. A module-scope `import mlx_lm` anywhere — or an eager PyInstaller
`hiddenimport`, or a capability probe that loads a tokenizer from a request handler — binds the
stream to the wrong thread and produces failures that are non-obvious and non-recoverable.
Upstream is explicit that this will not be fixed soon (`awni`, mlx#3078: "it will be a while
before we can provide true thread safety").

This is a grep, not a runtime test: the damage happens at import time, so by the time a runtime
assertion could run, the binding has already happened.
"""
import ast
import os
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Modules whose import binds MLX thread state.
WATCHED = ("mlx", "mlx_lm", "mlx_vlm", "mlx_embeddings")

# `services/mlx_engine.py` owns the single MLX thread, so a module-scope import there would be
# the one place it could be argued for — but it does not do it today, and if that changes it
# should be a deliberate decision, not a silent drift. No exemptions.
EXEMPT: set = set()


def _module_scope_mlx_imports(path: pathlib.Path):
    """Top-level (module-scope) imports of a watched module, with line numbers."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    hits = []
    for node in tree.body:                      # tree.body == module scope only
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in WATCHED:
                    hits.append((node.lineno, a.name))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in WATCHED:
                hits.append((node.lineno, node.module))
    return hits


def test_no_module_scope_mlx_imports():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        parts = set(rel.parts)
        if ".venv" in parts or "build" in parts or "dist" in parts or "tests" in parts:
            continue
        if str(rel) in EXEMPT:
            continue
        for lineno, name in _module_scope_mlx_imports(path):
            offenders.append(f"{rel}:{lineno} imports {name} at module scope")

    assert not offenders, (
        "MLX imported at MODULE SCOPE — this binds mlx-lm's generation_stream to whichever "
        "thread imports the module first, and KV arrays inherit that affinity irrecoverably "
        "(mlx-lm#1256, open on our pin). Move the import inside the function that uses it.\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_actually_detects_an_offender(tmp_path):
    """A grep-style guard that never fires is indistinguishable from one that cannot."""
    bad = tmp_path / "bad.py"
    bad.write_text("import mlx_lm\n\ndef f():\n    return 1\n")
    assert _module_scope_mlx_imports(bad) == [(1, "mlx_lm")]

    nested = tmp_path / "good.py"
    nested.write_text("def f():\n    import mlx_lm\n    return mlx_lm\n")
    assert _module_scope_mlx_imports(nested) == []

    from_import = tmp_path / "bad2.py"
    from_import.write_text("from mlx_lm.generate import stream_generate\n")
    assert _module_scope_mlx_imports(from_import) == [(1, "mlx_lm.generate")]
