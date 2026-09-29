"""Rebuild account-library search indexes after catalog mutations."""
from __future__ import annotations

import threading
from pathlib import Path

from core.library_operation_lock import serialize_library_operations


def rebuild_indexes(*, db_path: Path, model_name: str, on_finished) -> None:
    """Rebuild a library's FAISS indexes after rows leave or return.

    Upload processing and index rebuilding share a per-library lock because
    both rewrite the same index files. Until rebuilding finishes the engine
    searches exactly, because its indexes no longer match the catalog size.
    """
    with serialize_library_operations(db_path):
        try:
            from core.indexer import create_faiss_indices

            create_faiss_indices(db_path, model_name)
        finally:
            on_finished()


def rebuild_indexes_in_background(*, db_path: Path, model_name: str, on_finished) -> None:
    threading.Thread(
        target=rebuild_indexes,
        kwargs={"db_path": db_path, "model_name": model_name, "on_finished": on_finished},
        name="iris-reindex",
        daemon=True,
    ).start()
