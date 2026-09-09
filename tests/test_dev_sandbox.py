from __future__ import annotations

from scripts.dev import ADMIN_PASSWORD, MARKER, MEMBER_PASSWORD, seed_sandbox


def test_dev_sandbox_creates_disposable_isolated_accounts(tmp_path):
    root = tmp_path / ".iris-dev"
    seed_sandbox(root)
    assert (root / MARKER).is_file()
    assert (root / "data" / "users.db").is_file()
    assert (root / "data" / "users" / "1" / "media" / "demo-admin.jpg").is_file()
    assert (root / "data" / "users" / "2" / "media" / "demo-familia.jpg").is_file()
    assert ADMIN_PASSWORD != MEMBER_PASSWORD


def test_dev_sandbox_is_idempotent(tmp_path):
    root = tmp_path / ".iris-dev"
    seed_sandbox(root)
    seed_sandbox(root)
    assert (root / MARKER).is_file()
