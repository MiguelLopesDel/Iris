"""Similar search on real catalogue shapes: fully embedded, or with a malformed row (#56)."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from core.indexer_db import init_db
from core.search_engine import IrisEngine

ROOT = Path(__file__).resolve().parents[1]


def _catalogue(db: Path, malformed: bytes | None = None, at: int = 0) -> None:
    """Four near-identical 768-d photos, plus one malformed row at ``at``."""
    rng = np.random.default_rng(7)
    base = np.ones(768, dtype=np.float32)
    rows = []
    for number in range(4):
        vector = (base + 0.1 * rng.standard_normal(768)).astype(np.float32)
        rows.append((f"IMG_{number:04d}.jpg", (vector / np.linalg.norm(vector)).tobytes()))
    if malformed is not None:
        rows.insert(at, ("BAD.jpg", malformed))
    conn = init_db(db)
    conn.executemany(
        "INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)",
        [(name, f"/media/{name}", blob) for name, blob in rows],
    )
    conn.commit()
    conn.close()


def _similar_counts(db: Path) -> dict[str, int]:
    engine = IrisEngine(db_path=str(db), load_model=False)
    return {
        engine.records[index].arquivo: len(engine.search_similar(index))
        for index in range(len(engine.records))
    }


def test_similar_search_on_a_fully_embedded_library_keeps_the_process_alive(tmp_path: Path):
    # Every row embedded: the matrix is a read-only memory map, and
    # normalising a row of it in place was a segmentation fault. Run apart, so
    # a crash fails this test instead of taking the test run down.
    db = tmp_path / "iris.db"
    _catalogue(db)
    script = (
        "import sys; from tests.test_similar_search import _similar_counts; "
        "print(sorted(_similar_counts(__import__('pathlib').Path(sys.argv[1])).values()))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(db)],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "[4, 4, 4, 4]"


@pytest.mark.parametrize("at", [0, 2, 4])
@pytest.mark.parametrize(
    "malformed",
    [b"\0" * 32, b"", b"\0" * 30],
    ids=["another-width", "empty", "not-float32"],
)
def test_a_malformed_embedding_leaves_similar_search_working_for_the_rest(
    tmp_path: Path, malformed: bytes, at: int
):
    db = tmp_path / "iris.db"
    _catalogue(db, malformed, at)

    counts = _similar_counts(db)

    assert counts.pop("BAD.jpg") == 0
    assert set(counts.values()) == {4}


def test_the_vector_width_is_the_one_most_rows_have(tmp_path: Path):
    from core import vector_sidecar

    db = tmp_path / "iris.db"
    _catalogue(db, b"\0" * 32, at=0)
    with sqlite3.connect(db) as conn:
        assert vector_sidecar.dominant_width(conn, "embedding") == 768
