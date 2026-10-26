"""Bounded, thread-safe cache of private-library backends."""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from core import compute_device
from core.backend import SearchBackend, create_backend
from core.embedding_models import resolve_embedding_model
from core.indexer_db import init_db
from core.users_db import IrisUser, get_user_by_id

logger = logging.getLogger(__name__)


class BackendRegistry:
    """Create one backend per account and retain only recently used instances.

    A backend loads the whole catalog, which takes seconds on a large library.
    Two ways to make it see new data:

    - :meth:`invalidate` drops it, so the next request builds a new one. For
      changes the person just made and expects to see at once.
    - :meth:`refresh` keeps serving it while a new one is built in the
      background, then swaps them. For changes arriving on their own (a phone
      syncing, an index rebuild): a burst of them costs one rebuild, and no
      request waits for it.
    """

    def __init__(
        self,
        users_db_path,
        *,
        cache_size: int = 1,
        load_model: bool = True,
        on_new_backend: Callable[[Path], None] | None = None,
        refresh_delay: float = 2.0,
    ):
        self.users_db_path = users_db_path
        self.cache_size = max(1, cache_size)
        self.load_model = load_model
        # Told the library's database path whenever a backend is built for it,
        # so caches keyed by that path drop what the previous backend saw.
        self._on_new_backend = on_new_backend
        self._backends: OrderedDict[int, SearchBackend] = OrderedDict()
        self._lock = threading.RLock()
        # Waits this long after a refresh before rebuilding, so the uploads of
        # one sync batch share a rebuild.
        self.refresh_delay = refresh_delay
        # Accounts whose backend is outdated and is (or will be) rebuilt.
        self._stale: set[int] = set()
        self._refreshing: set[int] = set()
        # Bumped by invalidate(): a background build started before it may
        # miss what the person just did, so it is discarded.
        self._epochs: dict[int, int] = {}
        # The embedding model each rebuild would otherwise read from disk again
        # (a large file, often on a busy disk), per checkpoint and device.
        self._encoders: dict[tuple[str, str], object] = {}

    def get_user(self, user_id: int) -> IrisUser | None:
        return get_user_by_id(self.users_db_path, user_id)

    def get(self, user_id: int) -> SearchBackend:
        with self._lock:
            existing = self._backends.pop(user_id, None)
            if existing is not None:
                self._backends[user_id] = existing
                return existing
            user = self.get_user(user_id)
            if user is None:
                raise KeyError(user_id)
            backend = self._build(user)
            self._install(user_id, user, backend)
            return backend

    def _build(self, user: IrisUser) -> SearchBackend:
        # Bring the library's schema and data repairs up to date before the
        # backend reads it: the gallery is often the first thing to open an
        # account after an upgrade, and the catalog it loads is cached.
        init_db(user.db_path).close()
        model_name = resolve_embedding_model(user.model_name)
        if not self.load_model:
            return create_backend(
                db_path=str(user.db_path), media_root=str(user.media_root),
                model_name=model_name, load_model=False,
            )
        device = compute_device.resolve("auto")
        key = (model_name, device)
        with self._lock:
            encoder = self._encoders.get(key)
        backend = create_backend(
            db_path=str(user.db_path), media_root=str(user.media_root),
            model_name=model_name, device=device, load_model=True, encoder=encoder,
        )
        loaded = getattr(getattr(backend, "engine", None), "model", None)
        if encoder is None and loaded is not None:
            with self._lock:
                # A model for another device is stale (the GPU choice changed).
                self._encoders = {k: v for k, v in self._encoders.items() if k[1] == device}
                self._encoders[key] = loaded
        return backend

    def _install(self, user_id: int, user: IrisUser, backend: SearchBackend) -> None:
        if self._on_new_backend is not None:
            self._on_new_backend(user.db_path)
        self._backends.pop(user_id, None)
        self._backends[user_id] = backend
        while len(self._backends) > self.cache_size:
            # Do not manually close here. A request that already holds the object
            # may still be searching; Python releases the evicted backend safely
            # when the final request reference is gone.
            evicted, _ = self._backends.popitem(last=False)
            self._stale.discard(evicted)

    def refresh(self, user_id: int) -> None:
        """Rebuild this account's backend in the background, serving the current one meanwhile."""
        with self._lock:
            if user_id not in self._backends:
                return  # nothing cached: the next request builds a fresh one
            self._stale.add(user_id)
            if user_id in self._refreshing:
                return  # the running rebuild sees the flag and goes again
            self._refreshing.add(user_id)
        threading.Thread(
            target=self._refresh_loop, args=(user_id,), name=f"iris-backend-refresh-{user_id}", daemon=True
        ).start()

    def _refresh_loop(self, user_id: int) -> None:
        try:
            while True:
                time.sleep(self.refresh_delay)
                with self._lock:
                    if user_id not in self._stale or user_id not in self._backends:
                        self._stale.discard(user_id)
                        return
                    self._stale.discard(user_id)
                    epoch = self._epochs.get(user_id, 0)
                user = self.get_user(user_id)
                if user is None:
                    return
                backend = self._build(user)
                with self._lock:
                    # Dropped meanwhile (invalidated, evicted): the next request builds its own.
                    if self._epochs.get(user_id, 0) == epoch and user_id in self._backends:
                        self._install(user_id, user, backend)
        except Exception:
            logger.exception("background rebuild of the library failed; it is rebuilt on next use")
            self.invalidate(user_id)
        finally:
            with self._lock:
                self._refreshing.discard(user_id)
                again = user_id in self._stale
            if again:
                self.refresh(user_id)

    def records_of(self, user_id: int):
        """The account's records for background work, without disturbing the cache.

        The cached backend when there is one; otherwise a detached one built
        just for this call (no model), so a busy account is never evicted.
        """
        with self._lock:
            cached = self._backends.get(user_id)
        if cached is not None:
            return cached.get_all_records()
        user = self.get_user(user_id)
        if user is None:
            return []
        init_db(user.db_path).close()
        detached = create_backend(
            db_path=str(user.db_path), media_root=str(user.media_root),
            model_name=resolve_embedding_model(user.model_name), load_model=False,
        )
        return detached.get_all_records()

    def invalidate(self, user_id: int) -> None:
        with self._lock:
            self._backends.pop(user_id, None)
            self._stale.discard(user_id)
            self._epochs[user_id] = self._epochs.get(user_id, 0) + 1

    def clear(self) -> None:
        with self._lock:
            self._backends.clear()
            self._encoders.clear()
