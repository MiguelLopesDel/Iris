from pathlib import Path

from core.backend_registry import BackendRegistry
from core.users_db import create_user


def test_registry_keeps_backends_separate_and_evicts_lru(tmp_path: Path, monkeypatch):
    users_db = tmp_path / "users.db"
    first = create_user(users_db, tmp_path, username="ana", password_hash="hash-ana")
    second = create_user(users_db, tmp_path, username="bia", password_hash="hash-bia")
    third = create_user(users_db, tmp_path, username="cai", password_hash="hash-cai")
    created: list[tuple[str, str]] = []

    def fake_backend(*, db_path, media_root, **_kwargs):
        result = object()
        created.append((db_path, media_root))
        return result

    monkeypatch.setattr("core.backend_registry.create_backend", fake_backend)
    registry = BackendRegistry(users_db, cache_size=2, load_model=False)
    first_backend = registry.get(first.id)
    second_backend = registry.get(second.id)
    assert first_backend is not second_backend
    assert created == [(str(first.db_path), str(first.media_root)), (str(second.db_path), str(second.media_root))]

    registry.get(first.id)  # first is most recently used; second becomes LRU
    registry.get(third.id)
    new_second_backend = registry.get(second.id)
    assert new_second_backend is not second_backend
