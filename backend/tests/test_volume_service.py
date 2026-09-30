"""LB-11: the encrypted volume.

These create a REAL encrypted sparsebundle and mount it. A mocked `hdiutil`
would prove nothing about the property under test — that the data is genuinely
unreadable without the key — and this is the module with the most power to
destroy the user's corpus, so it gets tested against the real thing.

Safety: every test uses a tmp_path data dir and a throwaway keychain service, and
the fixture detaches and deletes whatever it made. Nothing here can reach
`~/Library/Application Support/LocalBook`.
"""

import json
import os
import secrets
import subprocess
from pathlib import Path

import pytest

from services import keyvault, volume_service
from services.volume_service import VolumeError


@pytest.fixture
def vol(tmp_path, monkeypatch):
    """An isolated volume: temp mount point, throwaway keychain item."""
    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)

    from config import settings

    mount = tmp_path / "LocalBook"
    monkeypatch.setattr(settings, "data_dir", mount)
    monkeypatch.setattr(settings, "volume_max_size_gb", 1)   # 1 GB ceiling is plenty

    real_run = subprocess.run
    try:
        yield volume_service
    finally:
        # Detach unconditionally, NOT gated on is_mounted(): that is
        # sentinel-based, so it cannot see a volume that mounted but carried no
        # sentinel — which is exactly the case that leaked four mounts before
        # attach() learned to unmount itself on refusal.
        try:
            real_run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)
        except Exception:
            pass
        for purpose in keyvault.PURPOSES:
            real_run(
                ["security", "delete-generic-password", "-a", purpose, "-s", keyvault.service_name()],
                capture_output=True,
            )


# ── paths ───────────────────────────────────────────────────────────────────


def test_the_image_sits_beside_the_mount_point_never_inside(vol, tmp_path):
    """An image cannot contain its own mount point."""
    assert vol.image_path().parent == tmp_path
    assert vol.mount_point() not in vol.image_path().parents


def test_the_image_is_named_after_the_mount_point(vol, tmp_path, monkeypatch):
    """A fixed name would have the dev sandbox and production sharing ONE
    encrypted volume, each believing it owned the contents."""
    from config import settings

    assert vol.image_path().name == "LocalBook.sparsebundle"
    monkeypatch.setattr(settings, "data_dir", tmp_path / "LocalBook-dev")
    assert vol.image_path().name == "LocalBook-dev.sparsebundle"


# ── the sentinel is the whole of fail-closed ────────────────────────────────


def test_an_empty_mount_point_does_not_count_as_mounted(vol, tmp_path):
    """THE failure this guards. A failed attach leaves an ordinary empty
    directory — and an empty data dir is indistinguishable from a new install, so
    LocalBook would come up blank and start writing a fresh corpus over the top.
    """
    mount = tmp_path / "LocalBook"
    mount.mkdir()
    assert vol.is_mounted(mount) is False
    assert vol.read_sentinel(mount) is None


def test_a_missing_mount_point_does_not_count_as_mounted(vol):
    assert vol.is_mounted() is False


def test_a_directory_full_of_data_but_no_sentinel_is_not_mounted(vol, tmp_path):
    """Plaintext data sitting at the mount point is exactly the pre-migration
    state. It must not read as "the volume is up"."""
    mount = tmp_path / "LocalBook"
    mount.mkdir()
    (mount / "localbook.db").write_bytes(b"real data")
    assert vol.is_mounted(mount) is False


def test_an_unreadable_sentinel_is_treated_as_not_mounted(vol, tmp_path):
    mount = tmp_path / "LocalBook"
    mount.mkdir()
    (mount / vol.SENTINEL).write_text("{ not json")
    assert vol.is_mounted(mount) is False


# ── create ──────────────────────────────────────────────────────────────────


def test_creating_makes_a_real_encrypted_image(vol):
    state = vol.create()

    assert state.exists is True
    assert state.encrypted is True, "the image does not report itself encrypted"
    assert vol.image_path().is_dir()      # a sparsebundle is a directory


def test_creating_twice_is_refused(vol):
    vol.create()
    with pytest.raises(VolumeError, match="already exists"):
        vol.create()


def test_creating_does_not_touch_the_mount_point(vol, tmp_path):
    """Creating the volume and moving data into it are separate operations, so a
    failure here cannot cost anything."""
    mount = tmp_path / "LocalBook"
    mount.mkdir()
    (mount / "existing.json").write_text("{}")

    vol.create()

    assert (mount / "existing.json").read_text() == "{}"


# ── attach ──────────────────────────────────────────────────────────────────


def test_a_new_volume_mounts_and_carries_a_sentinel(vol):
    state = vol.initialise_new_volume()

    assert state.mounted is True
    assert state.sentinel_ok is True
    assert state.volume_id
    assert vol.is_mounted() is True


def test_data_written_inside_survives_a_detach_and_reattach(vol):
    vol.initialise_new_volume()
    (vol.mount_point() / "canary.txt").write_text("still here")

    vol.detach()
    assert vol.is_mounted() is False

    vol.attach()
    assert (vol.mount_point() / "canary.txt").read_text() == "still here"


def test_the_data_is_unreadable_from_the_image_itself(vol):
    """LB-11's "done when": the data dir is unreadable without the key."""
    vol.initialise_new_volume()
    (vol.mount_point() / "secret.txt").write_text("PLAINTEXT-MARKER-12345")
    vol.detach()

    blob = b""
    for band in vol.image_path().rglob("*"):
        if band.is_file():
            blob += band.read_bytes()

    assert b"PLAINTEXT-MARKER-12345" not in blob
    assert b"secret.txt" not in blob


def test_attaching_twice_is_a_success_not_an_error(vol):
    """Idempotence is required, not a nicety: the watchdog restarts the sidecar
    and LB-11 must survive five restarts in a row."""
    vol.initialise_new_volume()
    for _ in range(5):
        state = vol.attach()
        assert state.mounted is True


def test_attaching_without_an_image_refuses_and_says_the_data_is_not_lost(vol):
    with pytest.raises(VolumeError, match="not lost") as exc:
        vol.attach()
    assert "phrase" in str(exc.value)


def test_attaching_refuses_to_mount_over_existing_files(vol, tmp_path):
    """Mounting over real files HIDES them, and a user who then adds a source is
    writing into the volume while their old data sits invisible underneath."""
    vol.create()
    mount = tmp_path / "LocalBook"
    mount.mkdir(exist_ok=True)
    (mount / "localbook.db").write_bytes(b"pre-migration data")

    with pytest.raises(VolumeError, match="not empty"):
        vol.attach()

    assert (mount / "localbook.db").read_bytes() == b"pre-migration data"


def test_a_volume_with_no_sentinel_is_refused_after_mounting(vol):
    """An image that was never initialised, or somebody else's. Proceeding would
    write a fresh corpus into a stranger's volume."""
    vol.create()
    with pytest.raises(VolumeError, match="never initialised|not LocalBook"):
        vol.attach(initialise_sentinel=False)


# ── detach: the dangerous direction ─────────────────────────────────────────


def test_detaching_when_not_mounted_is_harmless(vol):
    assert vol.detach()["detached"] is True


def test_detaching_does_not_force_by_default(vol, monkeypatch):
    """Forcing while four SQLite WALs are live truncates one mid-checkpoint.
    That must never be the default path."""
    vol.initialise_new_volume()

    calls = []
    real_run = vol._run

    def watched(args, **kw):
        calls.append(args)
        if args[:2] == ["hdiutil", "detach"]:
            return subprocess.CompletedProcess(args, 1, "", "Resource busy")
        return real_run(args, **kw)

    monkeypatch.setattr(vol, "_run", watched)
    monkeypatch.setattr(vol, "_DETACH_BACKOFF", 0.01)

    with pytest.raises(VolumeError, match="Refusing to force"):
        vol.detach()

    assert not any("-force" in a for a in calls), "it forced without being asked"


def test_force_is_used_only_when_explicitly_allowed(vol, monkeypatch):
    vol.initialise_new_volume()
    forced = []
    real_run = vol._run

    def watched(args, **kw):
        if args[:2] == ["hdiutil", "detach"]:
            if "-force" in args:
                forced.append(args)
                return real_run(args, **kw)
            return subprocess.CompletedProcess(args, 1, "", "Resource busy")
        return real_run(args, **kw)

    monkeypatch.setattr(vol, "_run", watched)
    monkeypatch.setattr(vol, "_DETACH_BACKOFF", 0.01)

    result = vol.detach(allow_force=True)
    assert result["forced"] is True
    assert forced


def test_a_polite_detach_is_retried_before_giving_up(vol, monkeypatch):
    vol.initialise_new_волume() if False else vol.initialise_new_volume()
    attempts = []
    real_run = vol._run

    def watched(args, **kw):
        if args[:2] == ["hdiutil", "detach"] and "-force" not in args:
            attempts.append(args)
            if len(attempts) < 3:
                return subprocess.CompletedProcess(args, 1, "", "Resource busy")
        return real_run(args, **kw)

    monkeypatch.setattr(vol, "_run", watched)
    monkeypatch.setattr(vol, "_DETACH_BACKOFF", 0.01)

    result = vol.detach()
    assert result["detached"] is True
    assert result["forced"] is False
    assert len(attempts) == 3


# ── the password ────────────────────────────────────────────────────────────


def test_the_passphrase_comes_from_keyvault_and_is_wrapped(vol):
    phrase = keyvault.generate_recovery_phrase()
    keyvault.set_recovery_key(phrase)

    pw = vol.passphrase()

    assert len(pw) >= 40
    assert keyvault.wrapped_path("volume").exists()
    import base64

    assert base64.b64decode(pw) == keyvault.unwrap_with_phrase(phrase, "volume")


def test_the_passphrase_is_stable(vol):
    assert vol.passphrase() == vol.passphrase()


def test_a_volume_made_with_one_password_does_not_open_with_another(vol, monkeypatch):
    """The other half of "unreadable without the key"."""
    vol.initialise_new_volume()
    vol.detach()

    monkeypatch.setattr(vol, "passphrase", lambda: "a-completely-different-password")
    with pytest.raises(VolumeError, match="could not unlock"):
        vol.attach()


# ── housekeeping ────────────────────────────────────────────────────────────


def test_compacting_a_mounted_volume_reports_rather_than_raises(vol):
    """This runs as an unattended NIGHT job — raising on a perfectly normal
    state is just noise."""
    vol.initialise_new_volume()
    result = vol.compact()
    assert result["compacted"] is False
    assert "mounted" in result["reason"]


def test_compacting_a_detached_volume_uses_the_piped_passphrase(vol):
    """Without -stdinpass hdiutil ignores stdin and raises a GUI prompt; the
    _run timeout would then fail this, leaving the dialog behind."""
    vol.initialise_new_volume()
    vol.detach()
    result = vol.compact()
    assert result["compacted"] is True, result


def test_compacting_with_no_image_is_harmless(vol):
    assert vol.compact()["compacted"] is False


def test_state_reports_the_shape_of_things(vol):
    before = vol.state()
    assert before.exists is False and before.mounted is False

    vol.initialise_new_volume()
    after = vol.state()

    assert after.exists is True
    assert after.mounted is True
    assert after.encrypted is True
    assert after.volume_id
    assert after.free_bytes > 0


def test_encryption_is_reported_while_mounted_not_just_when_detached(vol):
    """`hdiutil isencrypted` FAILS on a mounted image ("Resource temporarily
    unavailable"), and the volume is mounted almost always in production — so
    probing alone reported encryption as unknown exactly when everything was
    working. The verdict is probed at creation, while detached, and recorded."""
    vol.initialise_new_volume()
    state = vol.state()

    assert state.mounted is True
    assert state.encrypted is True
    assert state.encrypted_source == "recorded at creation"


def test_encryption_is_probed_when_the_volume_is_detached(vol):
    vol.initialise_new_volume()
    vol.detach()
    state = vol.state()

    assert state.mounted is False
    assert state.encrypted is True
    assert state.encrypted_source == "probed"


def test_the_image_is_a_sparsebundle_not_a_dmg(vol):
    """`diskutil` picks the format from the extension. Passing a path without
    `.sparsebundle` silently produces a fixed-size .dmg that mounts perfectly,
    allocates the whole ceiling immediately, and makes `hdiutil compact` a no-op
    — a 512 GB file where a 600 MB bundle was intended."""
    vol.create()
    image = vol.image_path()

    assert image.is_dir(), "a sparsebundle is a directory of bands"
    assert image.suffix == ".sparsebundle"
    assert (image / "Info.plist").is_file()
    # Sparse: a 1 GB ceiling must not have allocated 1 GB.
    assert vol._dir_bytes(image) < 200 * 1024 ** 2


def test_a_refused_attach_does_not_leave_the_volume_mounted(vol, tmp_path):
    """Found by leaking four mounts in one run. attach() mounted, saw no
    sentinel, raised — and left the volume attached over the data dir where
    nothing was watching it. `is_mounted()` is sentinel-based, so neither the app
    nor the cleanup could see it to unmount it."""
    vol.create()
    mount = tmp_path / "LocalBook"

    with pytest.raises(VolumeError):
        vol.attach(initialise_sentinel=False)

    mounted = subprocess.run(["mount"], capture_output=True, text=True).stdout
    assert str(mount) not in mounted, "the refused attach left the volume mounted"
