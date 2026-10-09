"""Storing metadata read from a file guards against concurrent changes."""

from __future__ import annotations

import sqlite3

import numpy as np

from core.backend import create_backend
from core.indexer_db import init_db


def _backend_with_one_row(tmp_path, metadata_json: str):
    db_path = tmp_path / "catalog.db"
    init_db(db_path).close()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO memes (arquivo, caminho, embedding, metadata_json) VALUES (?, ?, ?, ?)",
            ("IMG_0001.jpg", str(tmp_path / "IMG_0001.jpg"), np.ones(768, dtype=np.float32).tobytes(), metadata_json),
        )
    backend = create_backend(db_path=str(db_path), media_root=str(tmp_path), load_model=False)
    return backend, db_path


def test_metadata_is_stored_when_the_row_still_holds_what_was_read(tmp_path):
    backend, db_path = _backend_with_one_row(tmp_path, '{"kind": "image"}')

    assert backend.replace_record_metadata_json(1, '{"kind": "image"}', '{"kind": "image", "gps": null}')

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT metadata_json FROM memes WHERE id = 1").fetchone()[0] == (
            '{"kind": "image", "gps": null}'
        )


def test_a_row_changed_meanwhile_is_left_alone(tmp_path):
    backend, db_path = _backend_with_one_row(tmp_path, '{"kind": "image", "device": "edited"}')

    assert not backend.replace_record_metadata_json(1, '{"kind": "image"}', '{"kind": "image", "gps": null}')

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT metadata_json FROM memes WHERE id = 1").fetchone()[0] == (
            '{"kind": "image", "device": "edited"}'
        )
