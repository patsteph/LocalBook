"""Every `settings.<attr>` in the codebase must exist on Settings.

WHY THIS EXISTS (2026-08-20): the v2.3.0 config collapse deleted `llm_provider`, but
`main.py`'s startup banner still printed it. That raised an AttributeError inside the
background startup task, so the backend came up, served HTTP, never reported ready, and the
Tauri shell killed and restarted it every ~30 seconds. **The app would not launch**, and the
backend log showed a clean shutdown with no error — the traceback died inside the task.

603 tests were green. None caught it, for a good reason: nothing imports `main.py`, because
under the dev venv `settings.data_dir` resolves to the REAL production data directory and
importing it runs migrations against the user's own files. That exemption left the single most
launch-critical module with no coverage at all.

So this checks STATICALLY — parse the source, resolve every attribute access against
`Settings.model_fields`. No imports, no side effects, and it covers `main.py` like any other
file. Three more live references were found the same way when it was first run
(`api/settings.py`'s `llm_provider`, two in `scripts/embedding_equivalence.py`).
"""
import ast
import pathlib

import pytest

from config import Settings

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Names commonly bound to the Settings singleton, including the short aliases used inside
# function-local imports (`from config import settings as _s`).
ALIASES = {"settings", "app_settings", "_settings", "_s", "_st", "settings_obj"}

# Set on the instance rather than declared as fields.
EXTRA_VALID = {"data_dir", "db_path"}

SKIP_DIRS = {".venv", "build", "local", "tests", "__pycache__"}


def _source_files():
    for f in BACKEND.rglob("*.py"):
        if SKIP_DIRS & set(f.parts):
            continue
        yield f


def _settings_refs(path: pathlib.Path):
    """(lineno, alias.attr, attr) for each Settings attribute access in a file.

    Only files that actually import config are considered — plenty of modules have an
    unrelated local named `settings` (a dict of options), and flagging those is noise that
    gets the whole check ignored.
    """
    src = path.read_text()
    if "from config import" not in src and "import config" not in src:
        return
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    for n in ast.walk(tree):
        if (isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id in ALIASES
                and not n.attr.startswith("_")):
            yield n.lineno, f"{n.value.id}.{n.attr}", n.attr


def test_no_module_references_a_removed_setting():
    valid = set(Settings.model_fields.keys()) | EXTRA_VALID
    bad = []
    for f in _source_files():
        for lineno, expr, attr in _settings_refs(f):
            if attr not in valid:
                bad.append(f"{f.relative_to(BACKEND)}:{lineno}  {expr}")
    assert not bad, (
        "These reference a Settings attribute that does not exist. Each one raises "
        "AttributeError the moment that line runs:\n  " + "\n  ".join(sorted(bad))
    )


def test_main_py_is_covered_by_this_check():
    """The whole point. If `main.py` ever stops importing config under a name this
    recognises, the check would silently skip the most launch-critical file in the app and
    keep passing."""
    refs = list(_settings_refs(BACKEND / "main.py"))
    assert refs, "main.py resolved zero settings references — the check is not seeing it"


@pytest.mark.parametrize("attr", ["main_model", "fast_model", "vision_model",
                                  "image_model", "embedding_model", "embedding_dim"])
def test_the_role_attributes_exist(attr):
    """Named explicitly so a rename shows up as "this role is gone" rather than as a pile of
    AttributeErrors at runtime."""
    assert attr in Settings.model_fields


@pytest.mark.parametrize("attr", ["llm_provider", "ollama_base_url", "ollama_model",
                                  "ollama_fast_model", "use_ollama_embeddings",
                                  "main_engine", "fast_engine", "vision_engine",
                                  "image_engine", "embed_engine",
                                  "mlx_main_model", "mlx_fast_model", "mlx_embedding_model"])
def test_the_removed_settings_stay_removed(attr):
    """Re-adding one of these would let the two-name-per-role confusion back in — the exact
    arrangement the collapse existed to end."""
    assert attr not in Settings.model_fields
