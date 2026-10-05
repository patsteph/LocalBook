"""Data Health shows the encrypted volume (LB-11): mounted?, size, free space, last compact."""
from pathlib import Path

import pytest

from services import data_health


@pytest.fixture
def vol(monkeypatch, tmp_path):
    from services import volume_gate, volume_service
    monkeypatch.setattr(volume_gate, "encryption_enabled", lambda: True)
    state = {"mounted": True, "free": 40 * 1024 ** 3}
    monkeypatch.setattr(volume_service, "state", lambda: volume_service.VolumeState(
        image_path=tmp_path / "x.sparsebundle", mount_point=tmp_path, exists=True,
        mounted=state["mounted"], image_bytes=1_100_000_000, free_bytes=state["free"]))
    stamp = tmp_path / "stamp"
    monkeypatch.setattr(data_health, "COMPACT_STAMP", stamp)
    return state, stamp


def _overall(v):
    return data_health._overall({"volume": v, "backup": {"configured": True, "count": 1},
                                 "drills": {"runs": 1, "last": {"ok": True}}})


def test_a_healthy_volume_reports_its_numbers(vol):
    state, stamp = vol
    stamp.write_text("compacted")
    v = data_health._volume()
    assert v["enabled"] and v["mounted"] and v["image_bytes"] == 1_100_000_000
    assert v["last_compact"] is not None
    assert _overall(v)["state"] == "healthy"


def test_not_mounted_is_a_problem(vol):
    state, _ = vol
    state["mounted"] = False
    assert "not mounted" in " ".join(_overall(data_health._volume())["problems"])


def test_low_free_space_inside_is_a_warning(vol):
    state, _ = vol
    state["free"] = 1024 ** 3
    assert any("2 GB" in w for w in _overall(data_health._volume())["warnings"])


def test_no_encryption_reports_nothing(monkeypatch):
    from services import volume_gate
    monkeypatch.setattr(volume_gate, "encryption_enabled", lambda: False)
    assert data_health._volume() == {"enabled": False}
