import threading
import time
from pathlib import Path

from pytest import MonkeyPatch

from core.backend_registry import BackendRegistry
from core.embedding_models import DEFAULT_MODEL
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


def test_environment_model_overrides_existing_user_model(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    users_db = tmp_path / "users.db"
    user = create_user(
        users_db,
        tmp_path,
        username="ana",
        password_hash="hash-ana",
        model_name=DEFAULT_MODEL,
    )
    selected_models: list[str] = []

    def fake_backend(**kwargs: object) -> object:
        selected_models.append(str(kwargs["model_name"]))
        return object()

    monkeypatch.setenv("IRIS_MODEL", "google/siglip2-base-patch16-224")
    monkeypatch.setattr("core.backend_registry.create_backend", fake_backend)

    BackendRegistry(users_db, load_model=False).get(user.id)

    assert selected_models == ["google/siglip2-base-patch16-224"]


def _counting_registry(tmp_path: Path, monkeypatch: MonkeyPatch):
    users_db = tmp_path / "users.db"
    user = create_user(users_db, tmp_path, username="ana", password_hash="hash-ana")
    built: list[object] = []
    lock = threading.Lock()

    def fake_backend(**_kwargs):
        result = object()
        with lock:
            built.append(result)
        return result

    monkeypatch.setattr("core.backend_registry.create_backend", fake_backend)
    registry = BackendRegistry(users_db, load_model=False, refresh_delay=0.05)
    return registry, user, built


def _wait_until(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_refresh_keeps_serving_the_current_backend_until_the_new_one_is_ready(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    release = threading.Event()
    registry, user, built = _counting_registry(tmp_path, monkeypatch)
    first = registry.get(user.id)

    def held_backend(**_kwargs):
        release.wait(5)
        result = object()
        built.append(result)
        return result

    monkeypatch.setattr("core.backend_registry.create_backend", held_backend)
    registry.refresh(user.id)
    # The rebuild is held: requests are answered by the backend they had, without waiting.
    assert registry.get(user.id) is first

    release.set()
    _wait_until(lambda: registry.get(user.id) is not first)
    assert registry.get(user.id) is built[-1]


def test_a_burst_of_refreshes_rebuilds_once(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    registry, user, built = _counting_registry(tmp_path, monkeypatch)
    registry.get(user.id)

    for _ in range(20):  # one phone batch: an upload finished after another
        registry.refresh(user.id)
    _wait_until(lambda: len(built) == 2)
    time.sleep(0.3)
    assert len(built) == 2


def test_an_invalidation_discards_a_rebuild_already_running(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    started, release = threading.Event(), threading.Event()
    registry, user, built = _counting_registry(tmp_path, monkeypatch)
    registry.get(user.id)

    def slow_backend(**_kwargs):
        started.set()
        release.wait(5)
        result = object()
        built.append(result)
        return result

    monkeypatch.setattr("core.backend_registry.create_backend", slow_backend)
    registry.refresh(user.id)
    assert started.wait(5)
    stale_build = len(built)

    # The person renames a photo meanwhile: the build that started before must not win.
    def quick_backend(**_kwargs):
        result = object()
        built.append(result)
        return result

    monkeypatch.setattr("core.backend_registry.create_backend", quick_backend)
    registry.invalidate(user.id)
    fresh = registry.get(user.id)
    release.set()
    _wait_until(lambda: len(built) == stale_build + 2)
    time.sleep(0.2)
    assert registry.get(user.id) is fresh


def test_refresh_without_a_cached_backend_builds_nothing(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    registry, user, built = _counting_registry(tmp_path, monkeypatch)
    registry.refresh(user.id)
    time.sleep(0.2)
    assert built == []

def test_records_for_background_work_never_evict_the_cached_backend(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    users_db = tmp_path / "users.db"
    busy = create_user(users_db, tmp_path, username="ana", password_hash="hash-ana")
    other = create_user(users_db, tmp_path, username="bia", password_hash="hash-bia")

    class FakeBackend:
        def __init__(self, name: str):
            self.name = name

        def get_all_records(self):
            return [self.name]

    monkeypatch.setattr(
        "core.backend_registry.create_backend", lambda *, db_path, **_k: FakeBackend(db_path)
    )
    registry = BackendRegistry(users_db, cache_size=1, load_model=False)
    in_use = registry.get(busy.id)

    assert registry.records_of(busy.id) == [str(busy.db_path)]
    assert registry.records_of(other.id) == [str(other.db_path)]
    # The account being browsed keeps its backend: nothing was rebuilt for it.
    assert registry.get(busy.id) is in_use
