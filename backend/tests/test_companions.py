"""Companions — install, connect, and the shared-engine endpoint.

A companion is a separate local tool LocalBook knows how to find, configure and
take input from. The first is Meeting Notes
(github.com/kvango/Meeting-Summarizer).

The point of the architecture is that everything tool-specific is DATA. These
tests therefore check the manifest contract rather than the one tool, so a
second companion is a new JSON file rather than a new test file.

The load-bearing behaviour is `write_config`: LocalBook edits a file it does not
own. It must change exactly the keys it declared and leave everything else —
the user's microphone choice, their email address, their comments — untouched.
"""
import json
from pathlib import Path

import pytest

from services import companions as svc


def _code_without_docstring(fn) -> str:
    """Source of `fn` with its docstring removed.

    Needed because several of these checks assert that the code does NOT call
    something — while the docstring names that very thing to explain why. Twice
    now a passing implementation has been failed by its own explanation.
    """
    import inspect
    import ast
    src = inspect.getsource(fn)
    doc = ast.get_docstring(ast.parse(src.lstrip()).body[0], clean=False)
    return src.replace(doc, "") if doc else src



@pytest.fixture(autouse=True)
def _isolated_key(tmp_path, monkeypatch):
    """The companion key lives in data_dir; keep each test's key its own."""
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)


# ── the manifest contract ───────────────────────────────────────────────────

def test_every_shipped_manifest_is_valid():
    """A malformed manifest silently disappears from the UI. Fail here instead."""
    manifests = svc.load_manifests()
    assert manifests, "no companion manifests were loaded"
    for m in manifests:
        assert m.get("id") and m.get("name"), m
        assert m.get("homepage"), f"{m['id']} has no homepage — users must be able to read the source"
        if "configure" in m:
            cfg = m["configure"]
            assert cfg.get("file") and cfg.get("keys"), f"{m['id']} configure block is incomplete"
        if "produces" in m:
            assert m["produces"].get("dir"), f"{m['id']} produces no directory"


def test_the_meeting_notes_manifest_points_at_our_engine():
    m = svc.get_manifest("meeting-notes")
    assert m is not None
    keys = m["configure"]["keys"]
    assert keys["BASE_URL"] == "{localbook_openai_base}"
    assert keys["LLM"] == "{localbook_main_model}"
    assert keys["API_KEY"] == "{companion_key}"


def test_placeholders_resolve_to_real_values():
    from config import settings
    m = svc.get_manifest("meeting-notes")
    desired = svc.desired_config(m)
    assert desired["BASE_URL"].endswith("/v1")
    assert str(settings.api_port) in desired["BASE_URL"]
    assert desired["LLM"] == settings.main_model
    assert desired["API_KEY"].startswith("lb-")


# ── the companion key ───────────────────────────────────────────────────────

def test_the_key_is_stable_across_reads():
    """Unlike the app token, which rotates every launch — a companion holds a
    config file on disk, so a rotating secret would break it on every restart."""
    a = svc.get_companion_key()
    b = svc.get_companion_key()
    assert a and a == b


def test_the_key_file_is_not_world_readable(tmp_path):
    svc.get_companion_key()
    mode = (tmp_path / "companion_key").stat().st_mode & 0o777
    assert mode == 0o600, f"companion key is {oct(mode)}"


def test_only_the_real_key_verifies():
    key = svc.get_companion_key()
    assert svc.verify_companion_key(key) is True
    assert svc.verify_companion_key("lb-wrong") is False
    assert svc.verify_companion_key("") is False


def test_revoking_cuts_everything_off_at_once():
    svc.get_companion_key()
    svc.revoke_companion_key()
    assert svc.verify_companion_key("anything") is False


# ── editing a file we do not own ────────────────────────────────────────────

REAL_CONFIG = '''EMAIL="me@example.com"
MIC_DEVICE="Microphone"
MIC_FALLBACK_DEVICE="MacBook Pro Microphone"
SYSTEM_DEVICE="BlackHole 2ch"
OUTPUT_DEVICE="Meeting Output"
# --- LLM provider (any OpenAI-compatible server). Edit these to switch. ---
# llama.cpp :8080/v1 (default) | Ollama :11434/v1 | LM Studio :1234/v1
LLM="gemma4:e4b"
BASE_URL="http://localhost:8080/v1"
API_KEY="not-needed"
'''


@pytest.fixture
def fake_companion(tmp_path, monkeypatch):
    """A manifest pointed at a throwaway config, shaped exactly like the real one."""
    cfg = tmp_path / "config.sh"
    cfg.write_text(REAL_CONFIG)
    m = json.loads(json.dumps(svc.get_manifest("meeting-notes")))
    m["configure"]["file"] = str(cfg)
    m["detect"] = {"support_dir": str(tmp_path)}
    m["produces"]["dir"] = str(tmp_path / "out")
    return m, cfg


def test_connecting_rewrites_only_the_keys_we_declared(fake_companion):
    m, cfg = fake_companion
    assert svc.write_config(m)["ok"]
    after = cfg.read_text()

    # ours, changed
    assert 'BASE_URL="http://127.0.0.1:' in after
    assert 'API_KEY="lb-' in after
    # theirs, untouched — including the comments
    assert 'EMAIL="me@example.com"' in after
    assert 'SYSTEM_DEVICE="BlackHole 2ch"' in after
    assert "# --- LLM provider" in after
    assert "llama.cpp :8080/v1 (default)" in after


def test_the_original_config_is_backed_up_before_the_first_edit(fake_companion):
    m, cfg = fake_companion
    svc.write_config(m)
    backup = Path(str(cfg) + ".localbook-backup")
    assert backup.is_file()
    assert backup.read_text() == REAL_CONFIG


def test_the_backup_is_never_overwritten_by_a_later_connect(fake_companion):
    """Otherwise the second connect backs up our own edit, and the user's
    original settings are gone for good."""
    m, cfg = fake_companion
    svc.write_config(m)
    svc.write_config(m)
    backup = Path(str(cfg) + ".localbook-backup")
    assert backup.read_text() == REAL_CONFIG, "the backup was overwritten with our own edit"


def test_disconnecting_restores_what_was_there(fake_companion):
    m, cfg = fake_companion
    svc.write_config(m)
    assert cfg.read_text() != REAL_CONFIG
    assert svc.disconnect(m)["ok"]
    assert cfg.read_text() == REAL_CONFIG


def test_a_missing_key_is_appended_rather_than_lost(tmp_path, fake_companion):
    m, cfg = fake_companion
    cfg.write_text('EMAIL="x@y.com"\nMIC_DEVICE="Microphone"\n')
    assert svc.write_config(m)["ok"]
    after = cfg.read_text()
    assert 'API_KEY="lb-' in after
    assert 'EMAIL="x@y.com"' in after


def test_connecting_a_tool_with_no_config_file_fails_clearly(fake_companion):
    m, cfg = fake_companion
    cfg.unlink()
    result = svc.write_config(m)
    assert not result["ok"]
    assert "install" in result["error"].lower()


# ── state ───────────────────────────────────────────────────────────────────

def test_connected_means_its_config_actually_points_at_us(fake_companion):
    m, _ = fake_companion
    assert svc.is_connected(m) is False
    svc.write_config(m)
    assert svc.is_connected(m) is True


def test_a_tool_pointed_elsewhere_is_not_connected(fake_companion):
    """Connected is measured, not remembered — the user may have edited the
    config back to LM Studio by hand, and the card must tell the truth."""
    m, cfg = fake_companion
    svc.write_config(m)
    cfg.write_text(cfg.read_text().replace(
        svc.desired_config(m)["BASE_URL"], "http://localhost:1234/v1"))
    assert svc.is_connected(m) is False


def test_status_reports_one_word_for_the_card(fake_companion):
    m, _ = fake_companion
    assert svc.status(m)["state"] == "installed"
    svc.write_config(m)
    assert svc.status(m)["state"] == "connected"


def test_a_stale_pidfile_does_not_read_as_recording(fake_companion, tmp_path):
    m, _ = fake_companion
    pid = tmp_path / "recording.pid"
    pid.write_text("999999")        # a PID that cannot be alive
    m["control"]["state"]["pidfile"] = str(pid)
    assert svc.is_running(m) is False


def test_the_manifest_directory_ships_in_the_built_app():
    """PyInstaller only bundles data directories it is told about. Without an
    --add-data entry the manifests vanish and Settings → Companions is empty in
    the built app while working perfectly in dev — the same shape of bug that
    shipped image generation broken in v2.3.0.
    """
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    build = (backend / "build_backend.sh").read_text()
    assert "companions:companions" in build, (
        "companions/ is not --add-data'd in build_backend.sh — the manifests "
        "will not exist in the packaged app"
    )


def test_the_manifest_dir_is_resolved_relative_to_the_package():
    """It must not depend on the working directory: the packaged backend is
    launched by Tauri from somewhere else entirely."""
    import services.companions as c
    assert c._MANIFEST_DIR.is_absolute()
    assert c._MANIFEST_DIR.name == "companions"


# ── the installer is pinned, and checked before it runs ─────────────────────
#
# A one-click `curl | bash` from a GUI app is the classic supply-chain shape.
# Pinning a commit is half the answer; without a hash we still trust whatever
# the CDN hands back for that URL.

def test_the_installer_is_pinned_to_a_commit_not_a_branch():
    for m in svc.load_manifests():
        src = (m.get("install") or {}).get("source")
        if not src:
            continue
        ref = src.get("ref", "")
        assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
            f"{m['id']} is pinned to '{ref}' — must be a full commit SHA"
        )
        assert "/main/" not in (src.get("url") or ""), \
            f"{m['id']} install URL follows a branch"
        assert ref in (src.get("url") or ""), \
            f"{m['id']} install URL does not reference its pinned ref"


def test_every_pinned_installer_carries_a_checksum():
    for m in svc.load_manifests():
        src = (m.get("install") or {}).get("source")
        if not src:
            continue
        digest = src.get("sha256", "")
        assert len(digest) == 64, f"{m['id']} has no usable sha256"


def test_the_install_command_verifies_before_it_executes():
    """The order matters: download, check, THEN run. A command that pipes
    straight to bash has already executed by the time anything is verified."""
    cmd = svc.install_command(svc.get_manifest("meeting-notes"))
    assert "shasum -a 256 -c" in cmd
    assert "| bash" not in cmd, "piping to bash executes before the hash is checked"
    assert cmd.index("shasum") < cmd.index("bash "), "the check must precede execution"


def test_a_tampered_script_is_refused(monkeypatch):
    """The failure that matters. If the pinned revision no longer hashes to what
    we recorded, the answer is to stop — not to run it and hope."""
    class _Resp:
        def read(self): return b"#!/bin/bash\nrm -rf ~\n"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    result = svc.fetch_and_verify_script(svc.get_manifest("meeting-notes"))
    assert result["ok"] is False
    assert result["mismatch"] is True
    assert "has not been run" in result["error"]


def test_a_user_can_read_the_exact_revision_first():
    src = svc.install_source(svc.get_manifest("meeting-notes"))
    assert src["review_url"] and src["ref"] in src["review_url"]
    assert src["short_ref"] and src["ref_date"]


# ── outcome verification ────────────────────────────────────────────────────

def test_checks_look_for_artifacts_not_exit_codes():
    """`brew install … 2>/dev/null || true` exits 0 with no audio driver. The
    tool then records your mic and captures silence from the far side of every
    call, and nothing says so until you read the notes."""
    m = svc.get_manifest("meeting-notes")
    kinds = {c["kind"] for c in m["verify"]}
    assert "audio_device" in kinds, "nothing checks that the audio driver landed"
    assert "binary" in kinds


def test_every_check_can_explain_itself(monkeypatch):
    """A failed check with no remedy is just an accusation."""
    monkeypatch.setattr(svc, "_audio_devices", lambda: [])
    monkeypatch.setattr(svc, "_which", lambda b: None)
    result = svc.verify_install(svc.get_manifest("meeting-notes"))
    assert result["checked"] and not result["ok"]
    for c in result["checks"]:
        if not c["ok"]:
            assert c["fix"], f"{c['label']} failed with no guidance"
            assert c["label"], "a check with no label cannot be shown to anyone"


def test_partial_success_is_reported_as_partial(monkeypatch):
    """"Recording works, it just can't hear the far side" is far more useful
    than "install failed"."""
    monkeypatch.setattr(svc, "_which", lambda b: f"/opt/homebrew/bin/{b}")
    monkeypatch.setattr(svc, "_audio_devices", lambda: ["Mac mini Speakers"])
    monkeypatch.setattr(pathlib_exists_target := svc.Path, "exists", lambda self: True)
    result = svc.verify_install(svc.get_manifest("meeting-notes"))
    labels = {c["label"]: c["ok"] for c in result["checks"]}
    assert labels["the meeting command"] is True
    assert labels["the BlackHole audio driver"] is False
    assert result["failed_count"] >= 1 and not result["ok"]


def test_an_unknown_check_kind_fails_rather_than_passing():
    """A manifest typo must not quietly report a healthy install."""
    bogus = {"verify": [{"kind": "wishful_thinking", "label": "something"}]}
    result = svc.verify_install(bogus)
    assert result["ok"] is False
    assert "unknown check kind" in result["checks"][0]["detail"]


def test_audio_devices_are_read_without_the_tool_we_are_checking_for():
    """SwitchAudioSource is installed BY the thing under test — using it would
    be missing in exactly the failure case that matters."""
    code = _code_without_docstring(svc._audio_devices)
    assert "SwitchAudioSource" not in code
    # CoreAudio in-process is preferred; system_profiler remains the fallback.
    # The subprocess was a real flake source — seconds of latency on a busy
    # machine, inside a test suite.
    assert "list_devices" in code
    assert code.index("list_devices") < code.index("system_profiler")


# ── optional add-ons ────────────────────────────────────────────────────────
#
# Meeting Notes ships an optional SwiftBar plugin for menu-bar start/stop.
# Modelled generically: the second companion will have its own optional pieces,
# and "one toggle per add-on" should not need new code each time.

def test_extras_are_pinned_and_checksummed_like_the_installer():
    """An add-on is still remote code being placed on the user's machine."""
    for m in svc.load_manifests():
        for e in m.get("extras") or []:
            src = e.get("source") or {}
            assert len(src.get("sha256", "")) == 64, f"{m['id']}/{e['id']} has no checksum"
            assert len(src.get("ref", "")) == 40, f"{m['id']}/{e['id']} is not pinned to a commit"
            assert src["ref"] in src.get("url", ""), f"{m['id']}/{e['id']} URL ignores its pin"


def test_the_menubar_extra_needs_no_admin_password():
    """SwiftBar is an `app` cask, not a pkg — verified against Homebrew. That is
    what makes this add-on genuinely one click, unlike the main installer."""
    e = next(x for x in svc.get_manifest("meeting-notes")["extras"] if x["id"] == "menubar")
    assert e["cask_needs_admin"] is False


def test_the_plugin_folder_follows_the_users_choice(monkeypatch):
    """SwiftBar's folder is picked by the user on first launch. Writing to our
    default when they chose elsewhere installs a plugin that never loads and
    looks broken."""
    e = next(x for x in svc.get_manifest("meeting-notes")["extras"] if x["id"] == "menubar")

    class _Proc:
        returncode = 0
        stdout = "/Users/someone/MyPlugins\n"
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _Proc())
    assert str(svc._plugin_dir(e)) == "/Users/someone/MyPlugins"


def test_the_default_folder_is_used_when_nothing_is_configured(monkeypatch):
    e = next(x for x in svc.get_manifest("meeting-notes")["extras"] if x["id"] == "menubar")

    class _Proc:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: _Proc())
    assert "SwiftBar/Plugins" in str(svc._plugin_dir(e))


def test_a_tampered_extra_is_never_written(tmp_path, monkeypatch):
    """The checksum is checked BEFORE anything lands on disk."""
    e_target = tmp_path / "plugins" / "meeting-summarizer.10s.sh"
    monkeypatch.setattr(svc, "_extra_target", lambda e: e_target)
    monkeypatch.setattr(svc, "_cask_installed", lambda c: True)

    class _Resp:
        def read(self): return b"#!/bin/bash\necho pwned\n"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())

    result = svc.install_extra(svc.get_manifest("meeting-notes"), "menubar")
    assert result["ok"] is False and result.get("mismatch") is True
    assert not e_target.exists(), "a mismatched add-on was written to disk anyway"


def test_installing_the_extra_places_an_executable(tmp_path, monkeypatch):
    target = tmp_path / "plugins" / "meeting-summarizer.10s.sh"
    monkeypatch.setattr(svc, "_extra_target", lambda e: target)
    monkeypatch.setattr(svc, "_cask_installed", lambda c: True)

    manifest = svc.get_manifest("meeting-notes")
    real = next(x for x in manifest["extras"] if x["id"] == "menubar")
    payload = b"#!/bin/bash\necho hi\n"
    import hashlib
    real["source"]["sha256"] = hashlib.sha256(payload).hexdigest()

    class _Resp:
        def read(self): return payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    monkeypatch.setattr(svc, "get_manifest", lambda i: manifest)

    assert svc.install_extra(manifest, "menubar")["ok"]
    assert target.is_file()
    assert target.stat().st_mode & 0o111, "SwiftBar cannot run a plugin that is not executable"


def test_removing_the_extra_leaves_its_host_app_alone(tmp_path, monkeypatch):
    """Uninstalling SwiftBar to remove one plugin would destroy something we did
    not create — the user may run other plugins in it."""
    target = tmp_path / "p.sh"
    target.write_text("x")
    monkeypatch.setattr(svc, "_extra_target", lambda e: target)
    assert svc.remove_extra(svc.get_manifest("meeting-notes"), "menubar")["ok"]
    assert not target.exists()
    code = _code_without_docstring(svc.remove_extra)
    assert "uninstall" not in code and "--cask" not in code


def test_an_unknown_extra_is_refused():
    assert svc.install_extra(svc.get_manifest("meeting-notes"), "nope")["ok"] is False


# ── preflight: one password prompt, and LocalBook never sees the password ────
#
# Homebrew refuses to run as root (check-run-command-as-root), so the installer
# cannot simply be wrapped in an admin prompt. Only three things actually need
# elevation, and only one of them is a package: the BlackHole .pkg. Downloading
# it as the USER and installing it as root is what collapses the whole thing
# into a single native authorization.
#
# These tests never install anything — every subprocess is captured.

@pytest.fixture
def privileged_manifest():
    """A companion that DOES need elevation.

    Written here rather than borrowed from a shipped manifest: these tests are
    about the mechanism — one prompt, correct ordering, cleanup — and must keep
    working when a particular companion stops needing a password. meeting-notes
    did exactly that once its installer took over the Homebrew packages.
    """
    return {
        "id": "test-privileged",
        "name": "Privileged Test Tool",
        "preflight": {
            "label": "Prepare",
            "summary": "test",
            "formulae": [
                {"name": "ffmpeg", "binary": "ffmpeg", "why": "records audio",
                 "required": True},
                {"name": "switchaudio-osx", "binary": "SwitchAudioSource",
                 "why": "switches output", "required": False},
            ],
            "pkg_casks": [
                {"cask": "blackhole-2ch", "label": "BlackHole audio driver",
                 "audio_device": "BlackHole 2ch", "why": "hears the far side"},
            ],
            "admin_commands": [
                {"cmd": "/usr/bin/killall coreaudiod", "optional": True,
                 "label": "restart Core Audio", "why": "publishes the new device"},
            ],
            "audio_setup": {"multi_output": {
                "name": "Meeting Output", "uid": "com.test.mo",
                "include": ["BlackHole 2ch"], "why": "hear while recording"}},
        },
    }


@pytest.fixture
def captured(monkeypatch):
    """Record every command instead of running it."""
    calls = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    # The mock must behave like reality: a binary appears only AFTER its install
    # runs. A fixture that never lets one appear makes outcome verification fire
    # correctly and abort — which looks like a code failure and is not one.
    present = {"brew"}

    def _run(args, **kw):
        calls.append(list(args))
        p = _Proc()
        if len(args) >= 3 and args[1] == "install" and "--cask" not in args:
            present.add(args[2])
            if args[2] == "switchaudio-osx":
                present.add("SwitchAudioSource")
        if "--cache" in args:
            p.stdout = "/tmp/fake/BlackHole2ch.pkg\n"
        return p

    monkeypatch.setattr(svc.subprocess, "run", _run)
    monkeypatch.setattr(svc, "_which",
                        lambda b: f"/opt/homebrew/bin/{b}" if b in present else None)
    # The audio step waits up to 25s for a freshly installed driver to appear.
    # These tests are about the privileged step's ordering, not CoreAudio, and
    # a real wait here turns a 1-second file into a multi-minute one.
    monkeypatch.setattr(svc, "_ensure_audio_devices", lambda pre, log: None)
    monkeypatch.setattr(svc, "_audio_devices", lambda: [])
    monkeypatch.setattr(svc.Path, "is_file", lambda self: True)
    return calls


def test_the_plan_separates_what_needs_a_password(captured, privileged_manifest):
    plan = svc.preflight_plan(privileged_manifest)
    by_label = {s["label"]: s for s in plan["steps"]}
    assert by_label["ffmpeg"]["needs_admin"] is False
    assert by_label["switchaudio-osx"]["needs_admin"] is False
    assert by_label["BlackHole audio driver"]["needs_admin"] is True
    assert plan["will_prompt"] is True


def test_every_step_explains_why_it_is_needed():
    """A list of package names is not consent. The user is granting admin — they
    should be able to see what each piece is for."""
    for m in svc.load_manifests():
        if not m.get("preflight"):
            continue
        for step in svc.preflight_plan(m)["steps"]:
            assert step["why"], f"{m['id']}: {step['label']} has no explanation"


def test_homebrew_is_never_run_as_root(captured, privileged_manifest):
    """brew.sh has check-run-command-as-root and will refuse — and running a
    package manager as root is wrong regardless."""
    svc.run_preflight(privileged_manifest)
    for call in captured:
        joined = " ".join(call)
        if "brew" in joined:
            assert "osascript" not in joined
            assert "administrator privileges" not in joined
            assert not joined.startswith("sudo")


def test_exactly_one_password_prompt(captured, privileged_manifest):
    """The whole point. Two prompts for one install is the thing we are fixing."""
    svc.run_preflight(privileged_manifest)
    prompts = [c for c in captured
               if any("administrator privileges" in str(a) for a in c)]
    assert len(prompts) == 1, f"expected one authorization, got {len(prompts)}"


def test_the_privileged_step_installs_the_pkg_and_restarts_core_audio(captured, monkeypatch, privileged_manifest):
    """Both privileged actions ride inside the single authorization."""
    written = {}
    real_write = svc.Path.write_text

    def _capture(self, text, *a, **k):
        if self.name == "preflight.sh":
            written["script"] = text
        return real_write(self, text, *a, **k)
    monkeypatch.setattr(svc.Path, "write_text", _capture)

    svc.run_preflight(privileged_manifest)
    script = written.get("script", "")
    assert "/usr/sbin/installer -pkg" in script
    assert "BlackHole" in script
    assert "killall coreaudiod" in script


def test_an_optional_privileged_step_cannot_abort_the_driver_install(captured, monkeypatch, privileged_manifest):
    """A failed Core Audio restart is cosmetic; an aborted driver install is not.
    With `set -e` and no guard, the first would kill the second."""
    written = {}
    real_write = svc.Path.write_text

    def _capture(self, text, *a, **k):
        if self.name == "preflight.sh":
            written["script"] = text
        return real_write(self, text, *a, **k)
    monkeypatch.setattr(svc.Path, "write_text", _capture)

    svc.run_preflight(privileged_manifest)
    line = next(l for l in written["script"].splitlines() if "killall" in l)
    assert line.endswith("|| true")


def test_the_pkg_is_downloaded_as_the_user_before_any_prompt(captured, privileged_manifest):
    """brew verifies its own checksum on fetch, and a download needs no
    privilege — so the elevated step is only ever `installer`."""
    svc.run_preflight(privileged_manifest)
    flat = [" ".join(c) for c in captured]
    fetch = next(i for i, c in enumerate(flat) if "fetch --cask" in c)
    prompt = next(i for i, c in enumerate(flat) if "administrator privileges" in c)
    assert fetch < prompt, "the pkg must be downloaded before the password prompt"


def test_password_free_work_happens_before_the_prompt(captured, privileged_manifest):
    """If the user cancels, they are left with a partly prepared Mac rather than
    nothing — and re-running picks up where it stopped."""
    svc.run_preflight(privileged_manifest)
    flat = [" ".join(c) for c in captured]
    installs = [i for i, c in enumerate(flat) if "brew install" in c]
    prompt = next(i for i, c in enumerate(flat) if "administrator privileges" in c)
    assert installs and max(installs) < prompt


def test_cancelling_the_prompt_is_reported_as_cancelled_not_failed(monkeypatch, captured, privileged_manifest):
    inner = svc.subprocess.run            # the fixture's mock; keep its behaviour

    def _run(args, **kw):
        result = inner(args, **kw)
        if any("osascript" in str(a) for a in args):
            return type("P", (), {"returncode": 1, "stdout": "",
                                  "stderr": "execution error: User canceled. (-128)"})()
        return result
    monkeypatch.setattr(svc.subprocess, "run", _run)

    result = svc.run_preflight(privileged_manifest)
    assert result["ok"] is False
    assert result["cancelled"] is True
    assert "nothing was installed" in result["error"].lower()


def test_the_temporary_privileged_script_is_cleaned_up(captured, monkeypatch, privileged_manifest):
    removed = []
    monkeypatch.setattr(svc.shutil, "rmtree", lambda d, **k: removed.append(d))
    svc.run_preflight(privileged_manifest)
    assert removed, "the root-executed script was left on disk"


def test_success_is_judged_on_outcome_not_exit_code(monkeypatch, captured, privileged_manifest):
    """osascript returning 0 does not mean the driver landed."""
    monkeypatch.setattr(svc, "_audio_devices", lambda: [])       # still absent
    result = svc.run_preflight(privileged_manifest)
    assert result["ok"] is False
    assert "still missing" in result["error"].lower()


def test_a_machine_without_homebrew_is_told_so(monkeypatch, privileged_manifest):
    monkeypatch.setattr(svc, "_which", lambda b: None)
    result = svc.run_preflight(privileged_manifest)
    assert result["ok"] is False and "brew.sh" in result["error"]


def test_every_module_this_feature_adds_is_declared_to_pyinstaller():
    """Most of these are imported lazily inside functions. PyInstaller usually
    follows that, but `companions/` was already silently left out of one build —
    the same shape of bug that shipped image generation broken in v2.3.0 — and a
    missing module means a feature that works in dev and is absent in the app.

    Cheap insurance, checked here rather than discovered by a user.
    """
    from pathlib import Path
    build = (Path(__file__).resolve().parents[1] / "build_backend.sh").read_text()
    required = [
        "services.companions", "services.folder_watcher", "services.meeting_notes",
        "services.post_ingest", "services.smart_folder", "services.audio_devices",
        "api.folders", "api.companions", "api.openai_compat",
        "storage.folder_link_store", "storage.smart_folder_store",
    ]
    missing = [m for m in required if f"--hidden-import={m} " not in build]
    assert not missing, f"not declared to PyInstaller: {missing}"


# ── updates: pinned, but not frozen ─────────────────────────────────────────
#
# Pinning protects the user from a moving curl|bash target. It also freezes
# them — upstream could fix a real bug and nobody would hear about it. The pin
# stays; what moves is a check, and accepting remains an explicit click.

def _fake_fetch(monkeypatch, body: bytes, commit: dict | None = None):
    import json as _json

    def _f(url, timeout=30):
        if "api.github.com" in url:
            return _json.dumps(commit or {
                "sha": "b" * 40,
                "commit": {"committer": {"date": "2026-09-20T10:00:00Z"},
                           "message": "fix: handle spaces in the output path"},
                "author": {"login": "kvango"},
            }).encode()
        return body
    monkeypatch.setattr(svc, "_fetch", _f)


def test_an_unchanged_upstream_is_not_reported_as_an_update(monkeypatch):
    """Upstream can move a dozen times without the installer changing a byte.
    Reporting a README edit as an update teaches people to dismiss the badge
    that matters."""
    import hashlib
    m = svc.get_manifest("meeting-notes")
    pinned = m["install"]["source"]["sha256"]

    # Content whose hash equals the pin, for both tracked artifacts.
    def _f(url, timeout=30):
        if "install.sh" in url:
            return _FakeBytes(pinned)
        return _FakeBytes(m["extras"][0]["source"]["sha256"])
    monkeypatch.setattr(svc, "_fetch", _f)
    monkeypatch.setattr(hashlib, "sha256", _fake_sha256)

    result = svc.check_updates(m)
    assert result["has_updates"] is False
    assert result["summary"] == "Up to date."


class _FakeBytes(bytes):
    """Carries the digest it should hash to, so tests need no real payloads."""
    def __new__(cls, digest: str):
        obj = super().__new__(cls, b"x")
        obj.digest_value = digest
        return obj


def _fake_sha256(data):
    class _H:
        def hexdigest(self_inner):
            return getattr(data, "digest_value", "different-" + str(len(data)))
    return _H()


def test_a_changed_installer_is_reported_with_somewhere_to_read_the_diff(monkeypatch):
    """"Something changed" without a diff is not information the user can act
    on — accepting means running someone else's code."""
    import hashlib
    _fake_fetch(monkeypatch, b"#!/bin/bash\n# genuinely new content\n")
    monkeypatch.setattr(hashlib, "sha256", _fake_sha256)

    result = svc.check_updates(svc.get_manifest("meeting-notes"))
    assert result["has_updates"] is True
    installer = next(a for a in result["artifacts"] if a["id"] == "install")
    assert installer["changed"] is True
    assert installer["compare_url"].startswith("https://github.com/")
    assert installer["current_ref"] in installer["compare_url"]
    assert installer["message"]


def test_a_check_never_advances_the_pin(monkeypatch, tmp_path):
    """THE safety property. Checking is passive: it must never change what the
    install command would run."""
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    import hashlib
    _fake_fetch(monkeypatch, b"new")
    monkeypatch.setattr(hashlib, "sha256", _fake_sha256)

    m = svc.get_manifest("meeting-notes")
    before = svc.install_command(m)
    svc.check_updates(m)
    assert svc.install_command(m) == before, "a passive check changed the pinned command"
    assert not (tmp_path / "companion_pins.json").exists()


def test_accepting_records_the_users_own_pin(monkeypatch, tmp_path):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    import hashlib
    _fake_fetch(monkeypatch, b"brand new installer")
    monkeypatch.setattr(hashlib, "sha256", _fake_sha256)

    m = svc.get_manifest("meeting-notes")
    result = svc.accept_update(m, "install")
    assert result["ok"] and result["ref"] == "b" * 7

    pins = json.loads((tmp_path / "companion_pins.json").read_text())
    entry = pins["meeting-notes"]["install"]
    assert entry["ref"] == "b" * 40
    assert entry["accepted_at"]
    assert entry["review_url"].startswith("https://github.com/")


def test_the_accepted_pin_is_what_gets_verified_afterwards(monkeypatch, tmp_path):
    """The shipped manifest is the revision LocalBook reviewed; the overlay is
    the one this user consented to. Later checks must compare against theirs."""
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    import hashlib
    _fake_fetch(monkeypatch, b"new")
    monkeypatch.setattr(hashlib, "sha256", _fake_sha256)

    m = svc.get_manifest("meeting-notes")
    svc.accept_update(m, "install")
    src = svc.install_source(m)
    assert src["user_accepted"] is True
    assert src["ref"] == "b" * 40
    assert src["ref"] in svc.install_command(m) or src["sha256"] in svc.install_command(m)


def test_accepting_re_downloads_rather_than_trusting_the_check(monkeypatch, tmp_path):
    """The check's numbers may be minutes old, and the pin recorded here is what
    every later verification compares against — it must describe bytes we just
    saw."""
    code = _code_without_docstring(svc.accept_update)
    assert "_fetch(" in code
    assert "sha256" in code


def test_accepting_an_add_on_reinstalls_it_immediately(monkeypatch, tmp_path):
    """A plugin is a file we placed — a new pin with the old file still on disk
    would be a lie. The installer is different: it only takes effect when re-run,
    which is the user's call."""
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    import hashlib
    _fake_fetch(monkeypatch, b"new plugin")
    monkeypatch.setattr(hashlib, "sha256", _fake_sha256)
    called = {}

    def _fake_install_extra(manifest, extra_id):
        called["extra"] = extra_id
        return {"ok": True}
    monkeypatch.setattr(svc, "install_extra", _fake_install_extra)

    result = svc.accept_update(svc.get_manifest("meeting-notes"), "extra:menubar")
    assert result["ok"] and result["reinstalled"] is True
    assert called["extra"] == "menubar"


def test_accepting_the_installer_says_it_must_be_re_run(monkeypatch, tmp_path):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    import hashlib
    _fake_fetch(monkeypatch, b"new")
    monkeypatch.setattr(hashlib, "sha256", _fake_sha256)
    result = svc.accept_update(svc.get_manifest("meeting-notes"), "install")
    assert result["reinstalled"] is False
    assert "re-run" in result["note"].lower()


def test_an_unreachable_github_is_not_reported_as_an_update(monkeypatch):
    """Offline must never look like "nothing to update" OR like a change."""
    monkeypatch.setattr(svc, "_fetch", lambda url, timeout=30: None)
    result = svc.check_updates(svc.get_manifest("meeting-notes"))
    assert result["has_updates"] is False
    assert all(a["error"] for a in result["artifacts"])


def test_status_reads_the_cache_and_never_the_network(monkeypatch):
    """status() renders the Settings tab and is polled. A network round-trip
    there would make the UI wait on GitHub."""
    calls = []
    monkeypatch.setattr(svc, "_fetch", lambda *a, **k: calls.append(a) or None)
    svc.status(svc.get_manifest("meeting-notes"))
    assert calls == [], "status() reached out to the network"


def test_the_background_sweep_ignores_companions_that_are_not_installed(monkeypatch):
    """Telling someone a tool they do not have has an update is pure noise."""
    monkeypatch.setattr(svc, "is_installed", lambda m: False)
    checked = []
    monkeypatch.setattr(svc, "check_and_cache", lambda m: checked.append(m["id"]))
    svc.check_all_for_updates()
    assert checked == []


# ── reporting the cause, and not asking for a password we do not need ───────
#
# 2026-09-21 field report: three prerequisites green, and preparation failed
# with "Still missing: Meeting Output". That named the SYMPTOM. The reason had
# been collected two lines earlier and thrown away — the API raised an
# HTTPException, which carries only `detail`, so `warnings`, `log` and the
# CoreAudio status never reached anyone. The failure was undiagnosable on a
# machine we could not inspect.

@pytest.fixture
def ready(monkeypatch):
    """Everything installed except the audio device — the exact field state."""
    monkeypatch.setattr(svc, "_which", lambda b: f"/opt/homebrew/bin/{b}")
    monkeypatch.setattr(svc, "_audio_devices",
                        lambda: ["Mac mini Speakers", "BlackHole 2ch"])
    monkeypatch.setattr(svc.subprocess, "run",
                        lambda *a, **k: pytest.fail("no subprocess should run"))


def test_no_password_is_requested_when_nothing_needs_one(ready, monkeypatch):
    """With the driver already installed, the only work left is creating an
    audio device — a user-level CoreAudio call. Prompting for a password to run
    `killall coreaudiod` against a machine that is already settled is a dialog
    nobody can want, and it makes the app look like it asks for admin at random.
    """
    devices = ["Mac mini Speakers", "BlackHole 2ch"]
    monkeypatch.setattr(svc, "_audio_devices", lambda: list(devices))

    def _create(pre, log):
        devices.append("Meeting Output")     # the device really appears
        return {"ok": True, "created": True}
    monkeypatch.setattr(svc, "_ensure_audio_devices", _create)

    result = svc.run_preflight(svc.get_manifest("meeting-notes"))
    assert result["prompted"] is False
    assert result["ok"] is True


def test_the_audio_device_is_still_built_on_the_no_password_path(ready, monkeypatch):
    """Skipping the prompt must not skip the work — this is the path a machine
    takes on its second attempt, which is exactly when the device is missing."""
    called = {}

    def _create(pre, log):
        called["ran"] = True
        return {"ok": True, "created": True}
    monkeypatch.setattr(svc, "_ensure_audio_devices", _create)
    svc.run_preflight(svc.get_manifest("meeting-notes"))
    assert called.get("ran"), "the audio step was skipped along with the prompt"


def test_the_error_leads_with_the_cause_not_the_symptom(ready, monkeypatch):
    """"Still missing: Meeting Output" is true and useless. The CoreAudio status
    is what someone can act on."""
    monkeypatch.setattr(svc, "_ensure_audio_devices", lambda pre, log: {
        "ok": False, "status": 560226676,
        "error": "CoreAudio refused to create Meeting Output (status 560226676).",
    })
    result = svc.run_preflight(svc.get_manifest("meeting-notes"))
    assert result["ok"] is False
    assert "CoreAudio refused" in result["error"]
    assert "560226676" in result["error"]
    assert "Meeting Output" in result["error"]     # the symptom still appears
    assert result["details"]["status"] == 560226676


def test_a_failure_returns_its_evidence_rather_than_raising():
    """An HTTPException carries only `detail`. Raising discarded the warnings,
    the log and the CoreAudio status — everything that explained the failure."""
    code = _code_without_docstring(
        __import__("api.companions", fromlist=["preflight"]).preflight)
    assert "warnings" in code and "details" in code
    assert "raise HTTPException" not in code.split("run_preflight")[-1], \
        "the preflight failure path still raises, which drops its own evidence"


# ── not duplicating the installer's work ────────────────────────────────────
#
# 2026-09-21 field report: "it installed a bunch of stuff I thought was covered
# as part of our install of the companion."
#
# It was. Twice. The preflight installed ffmpeg, switchaudio-osx and BlackHole;
# then the companion's own installer ran `brew install ffmpeg switchaudio-osx
# blackhole-2ch` with no guard and did all three again. Worse, we installed the
# driver with `installer -pkg`, which leaves no Caskroom entry — so brew had no
# record of it, ran the pkg a second time, and asked for a SECOND password for
# the thing we had just promised to handle once.

def test_we_do_not_pre_install_what_the_installer_installs():
    """The division of labour: their installer owns everything Homebrew owns.
    We own only what it cannot do."""
    m = svc.get_manifest("meeting-notes")
    pre = m.get("preflight") or {}
    assert not pre.get("formulae"), (
        "the companion's own installer runs `brew install` for these — "
        "pre-installing them is duplicated work, not a head start")
    assert not pre.get("pkg_casks"), (
        "installing a cask's pkg directly leaves no Caskroom entry, so brew "
        "reinstalls it and the user is asked for a second password")


def test_the_only_preparation_left_is_the_one_they_cannot_do():
    m = svc.get_manifest("meeting-notes")
    pre = m["preflight"]
    assert pre.get("audio_setup"), "the Multi-Output Device is the whole point"
    assert pre.get("when") == "after_install", (
        "it builds on a driver their installer provides, so offering it first "
        "would fail every time")


def test_preparation_now_needs_no_password_at_all():
    plan = svc.preflight_plan(svc.get_manifest("meeting-notes"))
    assert plan["will_prompt"] is False
    assert all(not s["needs_admin"] for s in plan["steps"])


def test_the_install_command_does_not_stall_on_a_question():
    """Homebrew asked "proceed? [y/n]" twice mid-install, which a user pasting a
    command into a terminal has no reason to expect — and auto-updated every tap
    first, printing a page of unrelated new formulae."""
    cmd = svc.install_command(svc.get_manifest("meeting-notes"))
    assert "NONINTERACTIVE=1" in cmd
    assert "HOMEBREW_NO_AUTO_UPDATE=1" in cmd
    # Still verified before it runs — the quieting must not weaken the check.
    assert cmd.index("shasum") < cmd.index("bash ")


def test_the_notes_warn_about_what_we_cannot_prevent():
    """Their installer downloads a model for llama.cpp that goes unused once
    connected. We cannot skip it without editing their repository, so the least
    we can do is say so before the user starts."""
    notes = " ".join(svc.get_manifest("meeting-notes")["install"]["notes"]).lower()
    assert "llama.cpp" in notes
    assert "gb" in notes, "the size of the unused download is the part that matters"
    assert "password once" in notes


# ── making the results visible ──────────────────────────────────────────────
#
# 2026-09-21: "how do I know it created the right MIDI setup, and where does the
# menu bar appear — I toggled it and I see nothing."
#
# Both are the same failure: we did the work and gave the user no way to see it.
# The audio device was correct and unverifiable; SwiftBar was installed but
# never launched, so a plugin sat in a folder no running process was watching.

def test_the_audio_device_reports_what_it_is_made_of():
    """A Multi-Output Device is only right if it has BOTH halves: something
    audible, and the loopback that captures the far side. One without the other
    silently yields a call you cannot hear, or a recording with no other party —
    and neither announces itself."""
    summary = svc._audio_summary(svc.get_manifest("meeting-notes"))
    assert summary is not None
    assert "members" in summary and "ok" in summary and summary["why"]


@pytest.mark.parametrize("members,expected_ok,expect_why", [
    (["Mac mini Speakers", "BlackHole 2ch"], True, "same time"),
    (["BlackHole 2ch"], False, "not hear"),
    (["Mac mini Speakers"], False, "loopback"),
])
def test_a_half_built_device_is_reported_as_wrong(monkeypatch, members,
                                                  expected_ok, expect_why):
    monkeypatch.setattr(svc, "_audio_summary", svc._audio_summary)
    import services.audio_devices as ad
    monkeypatch.setattr(ad, "describe_multi_output", lambda name: {
        "name": name, "id": 1, "uid": "u", "can_output": True,
        "is_default_output": False, "members": [], "member_names": members,
    })
    summary = svc._audio_summary(svc.get_manifest("meeting-notes"))
    assert summary["ok"] is expected_ok, summary
    assert expect_why in summary["why"]


def test_a_missing_device_is_reported_as_missing(monkeypatch):
    import services.audio_devices as ad
    monkeypatch.setattr(ad, "describe_multi_output", lambda name: None)
    summary = svc._audio_summary(svc.get_manifest("meeting-notes"))
    assert summary["exists"] is False and summary["ok"] is False


def test_the_menu_bar_host_is_started_not_merely_installed():
    """Installing an app puts it in /Applications; it does not run it."""
    code = _code_without_docstring(svc.install_extra)
    assert "_launch_host" in code


def test_the_host_is_told_where_the_plugin_is_before_it_starts():
    """SwiftBar asks for a plugin folder on first run. If the user picks a
    different one, the plugin we placed is never loaded."""
    code = _code_without_docstring(svc.install_extra)
    assert code.index("_point_host_at_plugins") < code.index("_launch_host")


def test_a_plugin_folder_the_user_already_chose_is_not_overwritten(monkeypatch):
    calls = []

    class _Proc:
        returncode = 0
        stdout = "/Users/someone/MyPlugins\n"
        stderr = ""

    def _run(args, **kw):
        calls.append(list(args))
        return _Proc()
    monkeypatch.setattr(svc, "_run_as_user", _run)

    e = next(x for x in svc.get_manifest("meeting-notes")["extras"] if x["id"] == "menubar")
    assert svc._point_host_at_plugins(e, svc.Path("/our/guess")) is False
    assert not any("write" in c for c in calls), "it overwrote the user's own choice"


# ── filing a recorder's output ──────────────────────────────────────────────
#
# A meeting recorder's notes are ABOUT DIFFERENT PEOPLE. Filing every 1:1 into
# one notebook makes the corpus less useful the more you record, and it is the
# case Smart Folders exist for: the notes name their participants, so each
# recording can be placed on its own.

def test_a_recorder_defaults_to_deciding_per_recording():
    routing = (svc.get_manifest("meeting-notes")["produces"].get("routing") or {})
    assert routing.get("default") == "smart"
    assert routing.get("why"), "a default this consequential should say why"


def test_connect_offers_three_distinct_modes():
    """"No notebook" previously meant two different things — file nothing, and
    file smartly — which is not a choice a user can express."""
    import inspect
    from api import companions as api
    src = inspect.getsource(api.connect)
    for mode in ("smart", "notebook", "none"):
        assert f'"{mode}"' in src, f"connect does not handle mode={mode}"


def test_smart_mode_links_the_folder_with_no_notebook():
    """notebook_id=None IS the Smart Folder — same scanning, destination decided
    per recording rather than fixed at connect time."""
    import inspect
    from api import companions as api
    src = inspect.getsource(api.connect)
    assert "target_notebook = req.notebook_id if mode == \"notebook\" else None" in src


def test_choosing_one_notebook_without_naming_it_falls_back_to_smart():
    """Better to decide per recording than to silently file nothing."""
    import inspect
    from api import companions as api
    src = inspect.getsource(api.connect)
    assert 'if mode == "notebook" and not req.notebook_id' in src


def test_a_smart_link_is_identifiable_from_status():
    """The card has to say "filed per recording" rather than showing a blank
    notebook name, which reads as something having gone wrong."""
    st = svc.status(svc.get_manifest("meeting-notes"))
    assert "linked_is_smart" in st
    assert st["routing_default"] == "smart"
