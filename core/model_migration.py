"""Moving a library to another embedding model without taking search down.

Vectors from two models are not comparable, so a library cannot be searched
while half of it has been re-encoded. With ``IRIS_MODEL_NEXT`` set, the new
vectors are computed (by an AI worker, see core/ai_jobs.py) into
``embedding_next`` beside the catalogue, while search keeps answering from the
current ones. When every item of a library has its new vectors, they replace
the old ones in one transaction and the library records the switch, from which
point it is searched with the new model. Libraries switch one by one; once all
have, ``IRIS_MODEL`` can be set to the new model and ``IRIS_MODEL_NEXT``
removed.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from core.embedding_models import resolve_embedding_model


def next_model() -> str:
    return (os.environ.get("IRIS_MODEL_NEXT") or "").strip()


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS embedding_next (
               meme_id INTEGER PRIMARY KEY,
               model_name TEXT NOT NULL,
               embedding_dim INTEGER NOT NULL,
               embedding BLOB NOT NULL,
               desc_embedding BLOB,
               FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS embedding_switch (
               model_name TEXT PRIMARY KEY,
               switched_at TEXT NOT NULL
           )"""
    )


def switched_to(db_path: Path | str, model: str) -> bool:
    """Whether this library has already moved to ``model``."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return False
    try:
        return conn.execute(
            "SELECT 1 FROM embedding_switch WHERE model_name = ?", (model,)
        ).fetchone() is not None
    except sqlite3.OperationalError:  # no table: never migrated
        return False
    finally:
        conn.close()


def library_model(db_path: Path | str, user_model: str) -> str:
    """The model this library's vectors are in, and so the one to search with."""
    configured = resolve_embedding_model(user_model)
    target = next_model()
    if target and target != configured and switched_to(db_path, target):
        return target
    return configured


def migration_target(db_path: Path | str, user_model: str) -> str:
    """The model a worker should produce vectors for, if this library is moving."""
    target = next_model()
    if target and library_model(db_path, user_model) != target:
        return target
    return ""


def progress(conn: sqlite3.Connection, target: str) -> tuple[int, int]:
    ensure_tables(conn)
    done = conn.execute(
        "SELECT COUNT(*) FROM embedding_next WHERE model_name = ?", (target,)
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0]
    return done, total


def complete_if_ready(conn: sqlite3.Connection, target: str) -> bool:
    """Swap in the new vectors once every item has them; True if it switched."""
    done, total = progress(conn, target)
    if not total or done < total:
        return False
    with conn:
        conn.execute(
            """UPDATE memes SET
                   embedding = (SELECT n.embedding FROM embedding_next n WHERE n.meme_id = memes.id),
                   desc_embedding = (SELECT n.desc_embedding FROM embedding_next n WHERE n.meme_id = memes.id),
                   embedding_dim = (SELECT n.embedding_dim FROM embedding_next n WHERE n.meme_id = memes.id),
                   model_name = ?
               WHERE id IN (SELECT meme_id FROM embedding_next WHERE model_name = ?)""",
            (target, target),
        )
        conn.execute("DELETE FROM embedding_next WHERE model_name = ?", (target,))
        conn.execute(
            "INSERT OR REPLACE INTO embedding_switch (model_name, switched_at) "
            "VALUES (?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
            (target,),
        )
    return True
