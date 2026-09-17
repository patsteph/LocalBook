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
    monkeypatch.setattr(svc, "_audio_devices", lambda: [])
    monkeypatch.setattr(svc.Path, "is_file", lambda self: True)
    return calls


def test_the_plan_separates_what_needs_a_password(captured):
    plan = svc.preflight_plan(svc.get_manifest("meeting-notes"))
    by_label = {s["label"]: s for s in plan["steps"]}
    assert by_label["ffmpeg"]["needs_admin"] is False
    assert by_label["switchaudio-osx"]["needs_admin"] is False
    assert by_label["BlackHole audio driver"]["needs_admin"] is True
    assert plan["will_prompt"] is True


def test_every_step_explains_why_it_is_needed():
    """A list of package names is not consent. The user is granting admin — they
    should be able to see what each piece is for."""
    plan = svc.preflight_plan(svc.get_manifest("meeting-notes"))
    for s in plan["steps"]:
        assert s["why"], f"{s['label']} has no explanation"


def test_homebrew_is_never_run_as_root(captured):
    """brew.sh has check-run-command-as-root and will refuse — and running a
    package manager as root is wrong regardless."""
    svc.run_preflight(svc.get_manifest("meeting-notes"))
    for call in captured:
        joined = " ".join(call)
        if "brew" in joined:
            assert "osascript" not in joined
            assert "administrator privileges" not in joined
            assert not joined.startswith("sudo")


def test_exactly_one_password_prompt(captured):
    """The whole point. Two prompts for one install is the thing we are fixing."""
    svc.run_preflight(svc.get_manifest("meeting-notes"))
    prompts = [c for c in captured
               if any("administrator privileges" in str(a) for a in c)]
    assert len(prompts) == 1, f"expected one authorization, got {len(prompts)}"


def test_the_privileged_step_installs_the_pkg_and_restarts_core_audio(captured, monkeypatch):
    """Both privileged actions ride inside the single authorization."""
    written = {}
    real_write = svc.Path.write_text

    def _capture(self, text, *a, **k):
        if self.name == "preflight.sh":
            written["script"] = text
        return real_write(self, text, *a, **k)
    monkeypatch.setattr(svc.Path, "write_text", _capture)

    svc.run_preflight(svc.get_manifest("meeting-notes"))
    script = written.get("script", "")
    assert "/usr/sbin/installer -pkg" in script
    assert "BlackHole" in script
    assert "killall coreaudiod" in script


def test_an_optional_privileged_step_cannot_abort_the_driver_install(captured, monkeypatch):
    """A failed Core Audio restart is cosmetic; an aborted driver install is not.
    With `set -e` and no guard, the first would kill the second."""
    written = {}
    real_write = svc.Path.write_text

    def _capture(self, text, *a, **k):
        if self.name == "preflight.sh":
            written["script"] = text
        return real_write(self, text, *a, **k)
    monkeypatch.setattr(svc.Path, "write_text", _capture)

    svc.run_preflight(svc.get_manifest("meeting-notes"))
    line = next(l for l in written["script"].splitlines() if "killall" in l)
    assert line.endswith("|| true")


def test_the_pkg_is_downloaded_as_the_user_before_any_prompt(captured):
    """brew verifies its own checksum on fetch, and a download needs no
    privilege — so the elevated step is only ever `installer`."""
    svc.run_preflight(svc.get_manifest("meeting-notes"))
    flat = [" ".join(c) for c in captured]
    fetch = next(i for i, c in enumerate(flat) if "fetch --cask" in c)
    prompt = next(i for i, c in enumerate(flat) if "administrator privileges" in c)
    assert fetch < prompt, "the pkg must be downloaded before the password prompt"


def test_password_free_work_happens_before_the_prompt(captured):
    """If the user cancels, they are left with a partly prepared Mac rather than
    nothing — and re-running picks up where it stopped."""
    svc.run_preflight(svc.get_manifest("meeting-notes"))
    flat = [" ".join(c) for c in captured]
    installs = [i for i, c in enumerate(flat) if "brew install" in c]
    prompt = next(i for i, c in enumerate(flat) if "administrator privileges" in c)
    assert installs and max(installs) < prompt


def test_cancelling_the_prompt_is_reported_as_cancelled_not_failed(monkeypatch, captured):
    inner = svc.subprocess.run            # the fixture's mock; keep its behaviour

    def _run(args, **kw):
        result = inner(args, **kw)
        if any("osascript" in str(a) for a in args):
            return type("P", (), {"returncode": 1, "stdout": "",
                                  "stderr": "execution error: User canceled. (-128)"})()
        return result
    monkeypatch.setattr(svc.subprocess, "run", _run)

    result = svc.run_preflight(svc.get_manifest("meeting-notes"))
    assert result["ok"] is False
    assert result["cancelled"] is True
    assert "nothing was installed" in result["error"].lower()


def test_the_temporary_privileged_script_is_cleaned_up(captured, monkeypatch):
    removed = []
    monkeypatch.setattr(svc.shutil, "rmtree", lambda d, **k: removed.append(d))
    svc.run_preflight(svc.get_manifest("meeting-notes"))
    assert removed, "the root-executed script was left on disk"


def test_success_is_judged_on_outcome_not_exit_code(monkeypatch, captured):
    """osascript returning 0 does not mean the driver landed."""
    monkeypatch.setattr(svc, "_audio_devices", lambda: [])       # still absent
    result = svc.run_preflight(svc.get_manifest("meeting-notes"))
    assert result["ok"] is False
    assert "still missing" in result["error"].lower()


def test_a_machine_without_homebrew_is_told_so(monkeypatch):
    monkeypatch.setattr(svc, "_which", lambda b: None)
    result = svc.run_preflight(svc.get_manifest("meeting-notes"))
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
