"""Registry of deleted media hashes — prevents re-indexing after intentional deletion."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"})
_PHASH_THRESHOLD = 8  # Hamming distance; ≤8 = visually identical (catches recompression)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deleted_media (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash TEXT UNIQUE NOT NULL,
            perceptual_hash TEXT,
            original_path TEXT,
            deleted_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_del_content ON deleted_media(content_hash)")


def _phash_str(path: str) -> str | None:
    """Perceptual hash for image files. None if unsupported or imagehash not installed."""
    if os.path.splitext(path)[1].lower() not in _IMAGE_EXTS:
        return None
    try:
        import imagehash
        from PIL import Image
        return str(imagehash.phash(Image.open(path).convert("RGB")))
    except Exception:
        return None


def register_deleted(db_path: str | Path, file_paths: list[str]) -> None:
    """Compute and store SHA256 + pHash for files about to be trashed.

    Must be called BEFORE move_to_trash while files still exist on disk.
    """
    if not file_paths:
        return
    from core.media_inventory import file_sha256

    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_table(conn)
        for fp in file_paths:
            if not fp or not os.path.exists(fp):
                continue
            try:
                sha = file_sha256(Path(fp))
                ph = _phash_str(fp)
                conn.execute(
                    "INSERT OR IGNORE INTO deleted_media (content_hash, perceptual_hash, original_path) VALUES (?, ?, ?)",
                    (sha, ph, fp),
                )
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()


def register_deleted_hashes(conn: sqlite3.Connection, content_hashes: list[str]) -> None:
    """Store content hashes when files are already gone (no pHash possible).

    Used by sync_index_after_trash which runs after files are already in the OS trash.
    """
    if not content_hashes:
        return
    _ensure_table(conn)
    conn.executemany(
        "INSERT OR IGNORE INTO deleted_media (content_hash) VALUES (?)",
        [(h,) for h in content_hashes if h],
    )


def load_deleted_content_hashes(conn: sqlite3.Connection) -> set[str]:
    """Return all content_hash values from deleted_media (empty set if table missing)."""
    try:
        _ensure_table(conn)
        return {
            row[0]
            for row in conn.execute(
                "SELECT content_hash FROM deleted_media WHERE content_hash IS NOT NULL"
            )
        }
    except sqlite3.OperationalError:
        return set()


def load_deleted_phashes(conn: sqlite3.Connection) -> list:
    """Return imagehash objects for all stored perceptual hashes.

    Empty list if imagehash is not installed or no entries exist.
    """
    try:
        import imagehash
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT perceptual_hash FROM deleted_media WHERE perceptual_hash IS NOT NULL"
        ).fetchall()
        result = []
        for (ph,) in rows:
            try:
                result.append(imagehash.hex_to_hash(ph))
            except Exception:
                continue
        return result
    except Exception:
        return []


def is_phash_deleted(new_path: str, deleted_phashes: list, threshold: int = _PHASH_THRESHOLD) -> bool:
    """Return True if new_path's pHash is within threshold of any deleted pHash.

    deleted_phashes should come from load_deleted_phashes() — load once per import run.
    """
    if not deleted_phashes or os.path.splitext(new_path)[1].lower() not in _IMAGE_EXTS:
        return False
    try:
        import imagehash
        from PIL import Image
        new_hash = imagehash.phash(Image.open(new_path).convert("RGB"))
        return any(new_hash - dph <= threshold for dph in deleted_phashes)
    except Exception:
        return False
