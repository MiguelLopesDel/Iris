"""Shared test helpers."""

from __future__ import annotations

import sqlite3

from core.concepts import create_concept_tables
from core.web_enrichment import create_web_enrichment_tables


def make_enrichment_conn(*, check_same_thread: bool = True) -> sqlite3.Connection:
    """In-memory SQLite with the meme + web-enrichment schema, for testing the
    enrichment job/endpoints without a real backend."""
    conn = sqlite3.connect(":memory:", check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arquivo TEXT,
            caminho TEXT,
            tags TEXT DEFAULT '',
            descricao_ia TEXT DEFAULT '',
            style TEXT DEFAULT '',
            source_work TEXT DEFAULT '',
            context TEXT DEFAULT ''
        )
        """
    )
    create_concept_tables(conn)
    create_web_enrichment_tables(conn)
    conn.execute(
        "INSERT INTO memes (id, arquivo, caminho) VALUES (1, 'x.jpg', '/x.jpg')"
    )
    conn.commit()
    return conn
