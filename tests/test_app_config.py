"""Unit tests for core/app_config.py — persisted settings + destination validation."""

from __future__ import annotations

from core import app_config


def test_load_defaults_when_missing(tmp_path):
    cfg = app_config.load(tmp_path / "nope.json")
    assert cfg["backup_auto"] is True
    assert cfg["backup_keep_last"] == 10
    assert cfg["backup_dir"] == ""


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    saved = app_config.save({"backup_dir": "/mnt/ext", "backup_keep_last": 3}, path)
    assert saved["backup_dir"] == "/mnt/ext" and saved["backup_keep_last"] == 3
    again = app_config.load(path)
    assert again["backup_dir"] == "/mnt/ext" and again["backup_keep_last"] == 3
    # Unknown keys are ignored.
    app_config.save({"bogus": 1}, path)
    assert "bogus" not in app_config.load(path)


def test_validate_empty_dir_errors(tmp_path):
    res = app_config.validate_backup_dir("", data_dir=tmp_path)
    assert res["ok"] is False and "error" in res


def test_validate_creates_and_accepts_dir(tmp_path):
    dest = tmp_path / "backups"
    res = app_config.validate_backup_dir(str(dest), data_dir=tmp_path)
    assert res["ok"] is True
    assert dest.exists()
    # dest and data_dir share a device here → expect the same-device warning.
    assert any("mesmo dispositivo" in w for w in res["warnings"])
