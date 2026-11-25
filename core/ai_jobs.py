"""Remote AI processing: another computer computes what this server cannot.

A server without a GPU catalogues uploads without AI (``IRIS_SYNC_AI_PROCESSING
=0``): no embeddings, OCR, taxonomy or faces, so search cannot reach them. A
worker on a computer with a GPU (``desktop/iris_ai_worker.py``) signs in as a
device of one account and pulls that library's items lacking AI: it claims a
batch, downloads the originals, runs the same indexer pipeline with the same
model, and posts back the columns and faces the server would have written.

The queue is not a separate list but the catalogue itself: an item without an
image embedding is pending. ``ai_jobs`` only holds leases (who is working on
what, until when) and failures, so a worker that disappears gives its items
back when the lease runs out, and a file the pipeline cannot read stops being
offered after a few attempts instead of looping forever.

Nothing a worker sends is trusted as-is: the item must be leased to that
device, the model must be this library's, vectors must have the declared
width and be finite, and only the AI columns are written.
"""
from __future__ import annotations

import base64
import binascii
import math
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from core import model_migration

LEASE_SECONDS = 15 * 60
MAX_ATTEMPTS = 3
MAX_CLAIM = 32
_MAX_TEXT = 20_000
_MAX_FACES = 64
_MAX_FACE_THUMB = 256 * 1024
_FACE_DIM = 512

# Text columns the pipeline fills, and the integer ones; nothing else is written.
TEXT_COLUMNS = (
    "texto_extraido", "ocr_normalized", "descricao_ia", "tags", "visual_json",
    "objects", "style", "source_work", "humor", "context",
)
INT_COLUMNS = ("width", "height", "schema_version")


class AiJobError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Job:
    media_id: int
    filename: str
    size: int
    sha256: str


def ensure_table(conn: sqlite3.Connection) -> None:
    model_migration.ensure_tables(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ai_jobs (
               meme_id INTEGER PRIMARY KEY,
               device_id TEXT NOT NULL DEFAULT '',
               lease_expires_at REAL NOT NULL DEFAULT 0,
               attempts INTEGER NOT NULL DEFAULT 0,
               last_error TEXT NOT NULL DEFAULT '',
               FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE
           )"""
    )


_NO_AI = "(m.embedding IS NULL OR length(m.embedding) = 0)"


SELECTIONS = ("", "new", "processed")


def _pending_where(target: str, only: str = "") -> tuple[str, tuple]:
    """Items still to process: without AI or, while moving, without new vectors.

    While moving, ``only`` narrows a trial: "new" takes items with no AI at
    all (they gain the most), "processed" items that already have it (they
    can be compared with the current model side by side).
    """
    if only not in SELECTIONS:
        raise AiJobError(400, "Seleção inválida")
    if target:
        where = "NOT EXISTS (SELECT 1 FROM embedding_next n WHERE n.meme_id = m.id AND n.model_name = ?)"
        if only == "new":
            where = f"{where} AND {_NO_AI}"
        elif only == "processed":
            where = f"{where} AND NOT {_NO_AI}"
        return where, (target,)
    # No image embedding yet (NULL, or an empty placeholder).
    return _NO_AI, ()


def pending_count(conn: sqlite3.Connection, target: str = "", only: str = "") -> int:
    ensure_table(conn)
    where, params = _pending_where(target, only)
    return conn.execute(
        f"""SELECT COUNT(*) FROM memes m LEFT JOIN ai_jobs j ON j.meme_id = m.id
            WHERE {where} AND COALESCE(j.attempts, 0) < ?""",
        (*params, MAX_ATTEMPTS),
    ).fetchone()[0]


def claim(
    conn: sqlite3.Connection, device_id: str, limit: int, now: float | None = None, target: str = "",
    only: str = "",
) -> list[Job]:
    """Lease up to ``limit`` pending items to this device, newest first.

    Newest first: what was just synced is what someone is about to look for.
    An item leased to another device is skipped until its lease runs out; one
    already leased to this device is offered again (a worker that restarted).
    """
    now = time.time() if now is None else now
    limit = max(1, min(MAX_CLAIM, int(limit)))
    ensure_table(conn)
    where, params = _pending_where(target, only)
    with conn:
        rows = conn.execute(
            f"""SELECT m.id, COALESCE(m.arquivo, ''), COALESCE(m.file_size, 0),
                       COALESCE(m.content_hash, '')
                FROM memes m LEFT JOIN ai_jobs j ON j.meme_id = m.id
                WHERE {where}
                  AND COALESCE(j.attempts, 0) < ?
                  AND (j.meme_id IS NULL OR j.lease_expires_at < ? OR j.device_id = ?)
                ORDER BY COALESCE(m.file_mtime, 0) DESC, m.id DESC
                LIMIT ?""",
            (*params, MAX_ATTEMPTS, now, device_id, limit),
        ).fetchall()
        conn.executemany(
            """INSERT INTO ai_jobs (meme_id, device_id, lease_expires_at) VALUES (?, ?, ?)
               ON CONFLICT(meme_id) DO UPDATE SET device_id = excluded.device_id,
                   lease_expires_at = excluded.lease_expires_at""",
            [(row[0], device_id, now + LEASE_SECONDS) for row in rows],
        )
    return [Job(int(r[0]), str(r[1]), int(r[2]), str(r[3])) for r in rows]


def require_lease(conn: sqlite3.Connection, media_id: int, device_id: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    ensure_table(conn)
    row = conn.execute(
        "SELECT device_id, lease_expires_at FROM ai_jobs WHERE meme_id = ?", (media_id,)
    ).fetchone()
    if row is None or row[0] != device_id or row[1] < now:
        raise AiJobError(409, "Este item não está reservado para este dispositivo")


def record_failure(conn: sqlite3.Connection, media_id: int, device_id: str, error: str) -> None:
    """Count a failed attempt and give the item back; after a few it is skipped."""
    require_lease(conn, media_id, device_id)
    with conn:
        conn.execute(
            """UPDATE ai_jobs SET attempts = attempts + 1, lease_expires_at = 0,
                   device_id = '', last_error = ? WHERE meme_id = ?""",
            (str(error)[:500], media_id),
        )


def _vector(value: Any, dim: int, name: str) -> bytes:
    try:
        raw = base64.b64decode(str(value), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AiJobError(400, f"{name}: base64 inválido") from exc
    if len(raw) != dim * 4:
        raise AiJobError(400, f"{name}: esperado {dim} valores float32")
    vector = np.frombuffer(raw, dtype=np.float32)
    if not np.all(np.isfinite(vector)):
        raise AiJobError(400, f"{name}: valores não finitos")
    return raw


def _validated(payload: Any, model_name: str) -> tuple[dict[str, Any], list[tuple]]:
    if not isinstance(payload, dict):
        raise AiJobError(400, "Resultado inválido")
    if payload.get("model_name") != model_name:
        raise AiJobError(409, f"Modelo diferente do da biblioteca ({model_name})")
    dim = payload.get("embedding_dim")
    if isinstance(dim, bool) or not isinstance(dim, int) or not 32 <= dim <= 4096:
        raise AiJobError(400, "embedding_dim inválido")
    columns: dict[str, Any] = {
        "model_name": model_name,
        "embedding_dim": dim,
        "embedding": _vector(payload.get("embedding"), dim, "embedding"),
    }
    if payload.get("desc_embedding"):
        columns["desc_embedding"] = _vector(payload["desc_embedding"], dim, "desc_embedding")
    for name in TEXT_COLUMNS:
        if name in payload:
            columns[name] = str(payload[name] or "")[:_MAX_TEXT]
    for name in INT_COLUMNS:
        value = payload.get(name)
        # Zero means "not measured" (the indexer leaves dimensions unset):
        # it must not erase what the catalogue already knows.
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 1_000_000:
            columns[name] = value

    faces = payload.get("faces") or []
    if not isinstance(faces, list) or len(faces) > _MAX_FACES:
        raise AiJobError(400, "faces inválido")
    face_rows: list[tuple] = []
    for face in faces:
        if not isinstance(face, dict):
            raise AiJobError(400, "faces inválido")
        score = face.get("det_score", 0)
        frame_time = face.get("frame_time")
        if not isinstance(score, (int, float)) or not math.isfinite(score):
            raise AiJobError(400, "det_score inválido")
        if frame_time is not None and (not isinstance(frame_time, (int, float)) or not math.isfinite(frame_time)):
            raise AiJobError(400, "frame_time inválido")
        thumbnail = None
        if face.get("thumbnail"):
            try:
                thumbnail = base64.b64decode(str(face["thumbnail"]), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise AiJobError(400, "miniatura de rosto inválida") from exc
            if len(thumbnail) > _MAX_FACE_THUMB:
                raise AiJobError(400, "miniatura de rosto grande demais")
        face_rows.append((
            str(face.get("bbox", ""))[:200], float(score), frame_time,
            _vector(face.get("embedding"), _FACE_DIM, "rosto"), thumbnail,
        ))
    return columns, face_rows


_VECTOR_COLUMNS = ("model_name", "embedding_dim", "embedding", "desc_embedding")
_OCR_COLUMNS = ("texto_extraido", "ocr_normalized")


def _current_vectors(value: Any, current_model: str) -> dict[str, Any]:
    """The current model's vectors a worker sent alongside, or nothing."""
    if not isinstance(value, dict) or value.get("model_name") != current_model:
        return {}
    try:
        raw = base64.b64decode(str(value.get("embedding", "")), validate=True)
    except (binascii.Error, ValueError):
        return {}
    dim = len(raw) // 4
    if not 32 <= dim <= 4096 or len(raw) != dim * 4:
        return {}
    vectors = {"model_name": current_model, "embedding_dim": dim,
               "embedding": _vector(value["embedding"], dim, "current_vectors")}
    if value.get("desc_embedding"):
        vectors["desc_embedding"] = _vector(value["desc_embedding"], dim, "current_vectors")
    return vectors


def store_result(
    conn: sqlite3.Connection, media_id: int, device_id: str, model_name: str, payload: Any,
    migrating: bool = False, current_model: str = "",
) -> None:
    """Write one worker result: the AI columns, the faces, and release the lease.

    ``model_name`` is the model the vectors must be in. While the library is
    moving to it (``migrating``), they go to ``embedding_next`` and search
    keeps using the current ones; an item that already had AI keeps its
    taxonomy (computed by the same CLIP either way) and takes only the newer
    reading of its text. Faces are written only for an item that has none, so
    reprocessing never undoes the people someone named. An item that had no
    AI also takes the current model's vectors when the worker sent them
    (``current_vectors``), so it is searchable before the library switches.
    """
    require_lease(conn, media_id, device_id)
    columns, faces = _validated(payload, model_name)
    current = conn.execute(
        "SELECT content_hash, embedding IS NOT NULL AND length(embedding) > 0 FROM memes WHERE id = ?",
        (media_id,),
    ).fetchone()
    if current is None:
        raise AiJobError(404, "Item não existe mais")
    if payload.get("sha256") and current[0] and payload["sha256"] != current[0]:
        raise AiJobError(409, "O arquivo processado não é o desta biblioteca")
    had_ai = bool(current[1])
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with conn:
        if migrating:
            conn.execute(
                """INSERT OR REPLACE INTO embedding_next
                   (meme_id, model_name, embedding_dim, embedding, desc_embedding) VALUES (?, ?, ?, ?, ?)""",
                (media_id, model_name, columns["embedding_dim"], columns["embedding"],
                 columns.get("desc_embedding")),
            )
            keep = _OCR_COLUMNS if had_ai else tuple(c for c in columns if c not in _VECTOR_COLUMNS)
            columns = {name: value for name, value in columns.items() if name in keep}
            if not had_ai and current_model:
                columns.update(_current_vectors(payload.get("current_vectors"), current_model))
        if columns:
            assignments = ", ".join(f"{name} = ?" for name in columns)
            conn.execute(f"UPDATE memes SET {assignments} WHERE id = ?", [*columns.values(), media_id])
        has_faces = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'faces'"
        ).fetchone()
        if has_faces and not conn.execute(
            "SELECT 1 FROM faces WHERE meme_id = ? LIMIT 1", (media_id,)
        ).fetchone():
            conn.executemany(
                """INSERT INTO faces (meme_id, person_id, bbox, det_score, frame_time, embedding,
                                      thumbnail, created_at) VALUES (?, NULL, ?, ?, ?, ?, ?, ?)""",
                [(media_id, *face, now) for face in faces],
            )
        conn.execute("DELETE FROM ai_jobs WHERE meme_id = ?", (media_id,))


def rebuild_vector_indices(db_path: Path) -> None:
    """Rewrite the image/description FAISS files from the catalogue.

    Search uses these files only when they hold one row per item, so filling
    in embeddings of existing items (same count) would leave an index that
    still answers with the old placeholders. Items still without a vector get
    a zero row, which scores nothing, so the row count stays aligned. Each
    file is written beside the old one and renamed over it.
    """
    import faiss

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT embedding, desc_embedding FROM memes ORDER BY id").fetchall()
    finally:
        conn.close()
    widths = [len(r[0]) // 4 for r in rows if r[0]]
    if not widths:
        return
    dim = max(set(widths), key=widths.count)

    def matrix(column: int) -> np.ndarray:
        out = np.zeros((len(rows), dim), dtype=np.float32)
        for i, row in enumerate(rows):
            blob = row[column]
            if blob and len(blob) == dim * 4:
                out[i] = np.frombuffer(blob, dtype=np.float32)
        return out

    prefix = db_path.with_suffix("")
    for column, suffix in ((0, "image"), (1, "desc")):
        data = matrix(column)
        norms = np.linalg.norm(data, axis=1, keepdims=True)
        data = data / np.maximum(norms, 1e-12)
        index = faiss.IndexFlatIP(dim)
        index.add(data)
        target = prefix.with_name(f"{prefix.name}_{suffix}.faiss")
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        os.close(fd)
        try:
            faiss.write_index(index, temporary)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
