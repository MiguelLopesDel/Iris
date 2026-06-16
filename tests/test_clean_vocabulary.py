"""Integration test for scripts/clean_vocabulary.py (dry-run vs --apply)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from core.concepts import create_concept_tables

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/clean_vocabulary.py"


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY, tags TEXT DEFAULT '')")
    create_concept_tables(conn)
    # A verbose concept that cleans to 'Frieren', colliding with an existing one.
    conn.execute(
        "INSERT INTO concepts (name, category, created_at) VALUES (?, 'personagem', 'x')",
        ("Frieren (Sousou no Frieren) personagem overpower",),
    )
    conn.execute(
        "INSERT INTO concepts (name, category, created_at) VALUES ('Frieren', 'personagem', 'x')"
    )
    conn.execute("INSERT INTO memes (id, tags) VALUES (1, 'frieren, VQA>x<loc_0>, N/A')")
    conn.commit()
    conn.close()
    return db


def _run(db: Path, *extra: str) -> None:
    subprocess.run(
        [sys.executable, SCRIPT, "--db", str(db), *extra], cwd=ROOT, check=True
    )


def test_dry_run_changes_nothing(tmp_path) -> None:
    db = _make_db(tmp_path)
    _run(db)  # no --apply

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 2
    assert conn.execute("SELECT tags FROM memes WHERE id = 1").fetchone()[0].count("loc_") == 1
    conn.close()


def test_apply_merges_concepts_and_cleans_tags(tmp_path) -> None:
    db = _make_db(tmp_path)
    _run(db, "--apply")

    conn = sqlite3.connect(db)
    names = sorted(r[0] for r in conn.execute("SELECT name FROM concepts"))
    tags = conn.execute("SELECT tags FROM memes WHERE id = 1").fetchone()[0]
    conn.close()

    assert names == ["Frieren"]  # verbose concept merged into the canonical one
    assert "loc_" not in tags and "frieren" in tags and "N/A" not in tags
