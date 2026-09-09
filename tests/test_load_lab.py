from __future__ import annotations

import json

from scripts.load_lab import MARKER, accounts_path, prepare


def test_load_lab_prepares_multiple_disposable_accounts(tmp_path):
    root = tmp_path / ".iris-load"
    prepare(root, users=2, records_per_user=3)
    assert (root / MARKER).is_file()
    accounts = json.loads(accounts_path(root).read_text(encoding="utf-8"))
    assert [account["username"] for account in accounts] == ["load-001", "load-002"]
    assert (root / "data" / "users" / "1" / "iris.db").is_file()
    assert (root / "data" / "users" / "2" / "media" / "load-sample.jpg").is_file()
