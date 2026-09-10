"""Bounded, thread-safe cache of private-library backends."""
from __future__ import annotations

import threading
from collections import OrderedDict

from core.backend import SearchBackend, create_backend
from core.embedding_models import resolve_embedding_model
from core.users_db import IrisUser, get_user_by_id


class BackendRegistry:
    """Create one backend per account and retain only recently used instances."""

    def __init__(self, users_db_path, *, cache_size: int = 1, load_model: bool = True):
        self.users_db_path = users_db_path
        self.cache_size = max(1, cache_size)
        self.load_model = load_model
        self._backends: OrderedDict[int, SearchBackend] = OrderedDict()
        self._lock = threading.RLock()

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
            backend = create_backend(
                db_path=str(user.db_path), media_root=str(user.media_root),
                model_name=resolve_embedding_model(user.model_name),
                load_model=self.load_model,
            )
            self._backends[user_id] = backend
            while len(self._backends) > self.cache_size:
                # Do not manually close here. A request that already holds the object
                # may still be searching; Python releases the evicted backend safely
                # when the final request reference is gone.
                self._backends.popitem(last=False)
            return backend

    def invalidate(self, user_id: int) -> None:
        with self._lock:
            self._backends.pop(user_id, None)

    def clear(self) -> None:
        with self._lock:
            self._backends.clear()
