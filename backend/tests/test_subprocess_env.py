"""A PyInstaller-bundled app is a hostile parent process.

2026-09-18 field report: preparing the Meeting Notes companion failed with
"Could not install switchaudio-osx … no bottle available … Tier 3
configuration … do not report any issues". The formula was fine — it installed
from a terminal on the same machine, seconds later.

What differed was the environment. `main.py` sets `CURL_CA_BUNDLE` to the
certifi bundle inside the app, which is right for our own HTTP clients. Homebrew
uses curl, inherited it, and on a network that inspects HTTPS — re-signing with
a private root macOS trusts and certifi has never heard of — could not fetch
bottle manifests. It concluded no bottle existed and suggested building from
source.

The same class of leak applies to PyInstaller's loader variables, which point
children at OUR copies of libssl and friends.
"""
import os
import sys

import pytest

from utils.subprocess_env import clean_child_env


@pytest.fixture
def bundled(monkeypatch):
    """Pretend we are running from inside a packaged app."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/Applications/LocalBook.app/Contents", raising=False)


def test_our_cert_bundle_is_not_forced_on_other_programs(bundled, monkeypatch):
    """THE regression. Homebrew's curl must use the system's trust, not ours."""
    monkeypatch.setenv("CURL_CA_BUNDLE",
                       "/Applications/LocalBook.app/Contents/certifi/cacert.pem")
    monkeypatch.setenv("SSL_CERT_FILE",
                       "/Applications/LocalBook.app/Contents/certifi/cacert.pem")
    env = clean_child_env()
    assert "CURL_CA_BUNDLE" not in env
    assert "SSL_CERT_FILE" not in env


def test_a_cert_bundle_the_user_set_is_left_alone(bundled, monkeypatch):
    """Corporate machines legitimately set these. Ours is ours to remove;
    theirs is not."""
    monkeypatch.setenv("CURL_CA_BUNDLE", "/etc/ssl/corporate-roots.pem")
    env = clean_child_env()
    assert env["CURL_CA_BUNDLE"] == "/etc/ssl/corporate-roots.pem"


def test_loader_paths_are_restored_to_their_originals(bundled, monkeypatch):
    """PyInstaller points DYLD_LIBRARY_PATH at the bundle and saves the real
    value in `_ORIG` precisely so it can be put back. A child that inherits ours
    tries to load our libssl."""
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/Applications/LocalBook.app/Contents/lib")
    monkeypatch.setenv("DYLD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    env = clean_child_env()
    assert env["DYLD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "DYLD_LIBRARY_PATH_ORIG" not in env


def test_a_loader_path_with_no_original_is_removed(bundled, monkeypatch):
    """No `_ORIG` means there was nothing there before we started."""
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/Applications/LocalBook.app/Contents/lib")
    monkeypatch.delenv("DYLD_LIBRARY_PATH_ORIG", raising=False)
    assert "DYLD_LIBRARY_PATH" not in clean_child_env()


def test_python_variables_do_not_leak(bundled, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/Applications/LocalBook.app/Contents")
    monkeypatch.setenv("PYTHONPATH", "/Applications/LocalBook.app/Contents/lib")
    env = clean_child_env()
    assert "PYTHONHOME" not in env and "PYTHONPATH" not in env


def test_homebrew_is_reachable_from_a_gui_app(monkeypatch):
    """A GUI app inherits launchd's PATH, which has no /opt/homebrew/bin."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = clean_child_env()
    assert "/opt/homebrew/bin" in env["PATH"].split(":")
    assert "/usr/bin" in env["PATH"].split(":")


def test_the_existing_path_keeps_its_order(monkeypatch):
    """Prepending brew would override a tool the user deliberately put first."""
    monkeypatch.setenv("PATH", "/my/tools:/usr/bin")
    parts = clean_child_env()["PATH"].split(":")
    assert parts[0] == "/my/tools"


def test_extras_win():
    env = clean_child_env({"HOMEBREW_NO_AUTO_UPDATE": "1"})
    assert env["HOMEBREW_NO_AUTO_UPDATE"] == "1"


def test_unbundled_runs_change_nothing_they_should_not(monkeypatch):
    """In development there is no bundle, so no value can be 'ours' — and a
    cert path must survive untouched."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", "/opt/homebrew/etc/ca-certificates/cert.pem")
    assert clean_child_env()["SSL_CERT_FILE"] == "/opt/homebrew/etc/ca-certificates/cert.pem"


def test_brew_is_never_handed_our_environment():
    """The fix has to be applied where it matters, not merely available."""
    import inspect
    from services import companions
    src = inspect.getsource(companions._run_as_user)
    assert "clean_child_env" in src
    assert "dict(os.environ)" not in src
