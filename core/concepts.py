from __future__ import annotations

import io
import sqlite3
from datetime import datetime
from typing import Any

import numpy as np
from PIL import Image


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def make_thumbnail(pil_image: Image.Image, size: int = 128) -> bytes:
    img = pil_image.copy().convert("RGB")
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def create_concept_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS concepts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'outro',
            search_terms TEXT NOT NULL DEFAULT '',
            auto_threshold REAL NOT NULL DEFAULT 0.65,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS concept_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            concept_id INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            thumbnail BLOB,
            label TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL,
            FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS concept_media (
            concept_id INTEGER NOT NULL,
            meme_id INTEGER NOT NULL,
            confirmed INTEGER NOT NULL DEFAULT 1,
            added_at TEXT NOT NULL,
            PRIMARY KEY (concept_id, meme_id),
            FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
            FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE
        )
        """
    )


def list_concepts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "concepts" not in tables:
        return []
    # Use LEFT JOINs instead of correlated subqueries — one pass, no per-row lookups
    rows = conn.execute(
        """
        SELECT
            c.id, c.name, c.category, c.description, c.search_terms, c.auto_threshold, c.created_at,
            COUNT(DISTINCT cr.id) AS ref_count,
            COUNT(DISTINCT cm.meme_id) AS assoc_count
        FROM concepts c
        LEFT JOIN concept_references cr ON cr.concept_id = c.id
        LEFT JOIN concept_media cm ON cm.concept_id = c.id AND cm.confirmed = 1
        GROUP BY c.id
        ORDER BY c.name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def create_concept(
    conn: sqlite3.Connection,
    name: str,
    description: str = "",
    category: str = "outro",
    search_terms: str = "",
    auto_threshold: float = 0.65,
) -> int:
    cursor = conn.execute(
        "INSERT INTO concepts (name, description, category, search_terms, auto_threshold, created_at) VALUES (?,?,?,?,?,?)",
        (name.strip(), description.strip(), category, search_terms.strip(), auto_threshold, _now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_concept(conn: sqlite3.Connection, concept_id: int, **fields: Any) -> None:
    allowed = {"name", "description", "category", "search_terms", "auto_threshold"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE concepts SET {set_clause} WHERE id = ?",
        list(updates.values()) + [concept_id],
    )
    conn.commit()


def delete_concept(conn: sqlite3.Connection, concept_id: int) -> None:
    conn.execute("DELETE FROM concept_media WHERE concept_id = ?", (concept_id,))
    conn.execute("DELETE FROM concept_references WHERE concept_id = ?", (concept_id,))
    conn.execute("DELETE FROM concepts WHERE id = ?", (concept_id,))
    conn.commit()


def add_reference(
    conn: sqlite3.Connection,
    concept_id: int,
    embedding_bytes: bytes,
    thumbnail_bytes: bytes | None,
    label: str = "",
) -> int:
    cursor = conn.execute(
        "INSERT INTO concept_references (concept_id, embedding, thumbnail, label, added_at) VALUES (?,?,?,?,?)",
        (concept_id, embedding_bytes, thumbnail_bytes, label.strip(), _now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def delete_reference(conn: sqlite3.Connection, ref_id: int) -> None:
    conn.execute("DELETE FROM concept_references WHERE id = ?", (ref_id,))
    conn.commit()


def get_references(conn: sqlite3.Connection, concept_id: int) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, embedding, thumbnail, label, added_at FROM concept_references WHERE concept_id = ? ORDER BY id",
        (concept_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_media_confirmed(
    conn: sqlite3.Connection, concept_id: int, meme_ids: list[int], confirmed: int = 1
) -> None:
    now = _now_iso()
    conn.executemany(
        "INSERT OR REPLACE INTO concept_media (concept_id, meme_id, confirmed, added_at) VALUES (?,?,?,?)",
        [(concept_id, mid, confirmed, now) for mid in meme_ids],
    )
    conn.commit()


def set_media_rejected(conn: sqlite3.Connection, concept_id: int, meme_ids: list[int]) -> None:
    set_media_confirmed(conn, concept_id, meme_ids, confirmed=0)


def get_confirmed_meme_ids(conn: sqlite3.Connection, concept_id: int) -> frozenset[int]:
    rows = conn.execute(
        "SELECT meme_id FROM concept_media WHERE concept_id = ? AND confirmed = 1",
        (concept_id,),
    ).fetchall()
    return frozenset(r[0] for r in rows)


def get_rejected_meme_ids(conn: sqlite3.Connection, concept_id: int) -> frozenset[int]:
    rows = conn.execute(
        "SELECT meme_id FROM concept_media WHERE concept_id = ? AND confirmed = 0",
        (concept_id,),
    ).fetchall()
    return frozenset(r[0] for r in rows)


def get_media_concepts(conn: sqlite3.Connection, meme_id: int) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "concepts" not in tables:
        return []
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.category, cm.confirmed
        FROM concepts c
        JOIN concept_media cm ON cm.concept_id = c.id
        WHERE cm.meme_id = ?
        ORDER BY c.name
        """,
        (meme_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_concept_meme_ids_for_filter(
    conn: sqlite3.Connection, concept_ids: frozenset[int]
) -> frozenset[int]:
    if not concept_ids:
        return frozenset()
    placeholders = ",".join("?" * len(concept_ids))
    rows = conn.execute(
        f"SELECT meme_id FROM concept_media WHERE concept_id IN ({placeholders}) AND confirmed = 1",
        list(concept_ids),
    ).fetchall()
    return frozenset(r[0] for r in rows)


def compute_centroid(reference_embeddings: list[bytes]) -> np.ndarray | None:
    if not reference_embeddings:
        return None
    arrays = [np.frombuffer(b, dtype=np.float32) for b in reference_embeddings]
    centroid = np.stack(arrays).mean(axis=0).reshape(1, -1).astype(np.float32)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid /= norm
    return centroid


def compute_refined_centroid(
    reference_embeddings: list[bytes],
    positive_extra: list[np.ndarray],
    negative_embeddings: list[np.ndarray],
    negative_weight: float = 0.25,
) -> np.ndarray | None:
    """Centroid refinado subtraindo a direção dos negativos explícitos."""
    all_positive = [np.frombuffer(b, dtype=np.float32) for b in reference_embeddings]
    all_positive += [v.reshape(-1).astype(np.float32) for v in positive_extra]
    if not all_positive:
        return None
    pos = np.stack(all_positive).mean(axis=0)
    if negative_embeddings:
        neg = np.stack([v.reshape(-1).astype(np.float32) for v in negative_embeddings]).mean(axis=0)
        pos = pos - negative_weight * neg
    centroid = pos.reshape(1, -1).astype(np.float32)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid /= norm
    return centroid
