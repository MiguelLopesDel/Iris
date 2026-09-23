"""Shared test helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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


# ── Guard: the test suite must never write to a real catalogue ──────────────
#
# A test once reached the developer's data/*.db through a code path that fell
# back to the configured database. Size and mtime of every real database are
# noted before the session and checked after it; any change fails the run.


_REAL_DATA = Path(__file__).resolve().parents[1] / "data"


def _real_databases() -> dict[str, tuple[int, int]]:
    if not _REAL_DATA.is_dir():
        return {}
    found = {}
    for path in _REAL_DATA.rglob("*.db"):
        try:
            stat = path.stat()
        except OSError:
            continue
        found[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return found


def pytest_sessionstart(session) -> None:
    session.config._iris_real_databases = _real_databases()


def pytest_sessionfinish(session, exitstatus) -> None:
    before = getattr(session.config, "_iris_real_databases", {})
    changed = sorted(
        path for path, state in _real_databases().items() if before.get(path, state) != state
    )
    if changed:
        session.exitstatus = 1
        print(f"\nERRO: os testes alteraram bancos reais em data/: {changed}", flush=True)
