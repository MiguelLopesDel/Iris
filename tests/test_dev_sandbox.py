from __future__ import annotations

from scripts.dev import CREDENTIALS_FILE, MARKER, sandbox_passwords, seed_sandbox


def test_dev_sandbox_creates_disposable_isolated_accounts(tmp_path):
    root = tmp_path / ".iris-dev"
    seed_sandbox(root)
    assert (root / MARKER).is_file()
    assert (root / "data" / "users.db").is_file()
    assert (root / "data" / "users" / "1" / "media" / "demo-admin.jpg").is_file()
    assert (root / "data" / "users" / "2" / "media" / "demo-familia.jpg").is_file()

    admin, member = sandbox_passwords(root)
    assert admin != member


def test_dev_sandbox_is_idempotent(tmp_path):
    root = tmp_path / ".iris-dev"
    seed_sandbox(root)
    seed_sandbox(root)
    assert (root / MARKER).is_file()


def test_sandbox_passwords_are_generated_not_hardcoded(tmp_path):
    """Duas sandboxes distintas não podem nascer com a mesma senha."""
    first, _ = sandbox_passwords(tmp_path / "a")
    second, _ = sandbox_passwords(tmp_path / "b")

    assert first != second
    assert len(first) >= 12


def test_sandbox_passwords_survive_a_second_run(tmp_path):
    """São impressas na criação; reusá-las depois exige persistência."""
    root = tmp_path / ".iris-dev"
    admin, member = sandbox_passwords(root)

    assert (root / CREDENTIALS_FILE).is_file()
    assert sandbox_passwords(root) == (admin, member)


def test_environment_overrides_the_generated_password(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_DEV_ADMIN_PASSWORD", "definida-pelo-ambiente")

    admin, _ = sandbox_passwords(tmp_path / ".iris-dev")

    assert admin == "definida-pelo-ambiente"
