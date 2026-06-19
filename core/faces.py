"""Reconhecimento facial — detecção, embedding (ArcFace/InsightFace), agrupamento.

Pipeline paralelo ao CLIP: cada mídia pode conter vários rostos; cada rosto vira uma linha
em ``faces`` (embedding 512-d normalizado + thumbnail recortado). O agrupamento por
similaridade preenche ``persons`` para a aba "Pessoas". O detector fica atrás de um seam
(``FaceDetector``) para que os testes injetem um fake sem baixar modelos/onnxruntime.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import numpy as np
from PIL import Image

from core.concepts import make_thumbnail

FACE_EMBED_DIM = 512
# Detecções abaixo deste score são descartadas (ruído / falsos positivos).
MIN_DET_SCORE = 0.50
# Dois rostos da MESMA mídia com cosseno >= isto são o mesmo rosto (dedup de frames de vídeo).
SAME_FACE_COSINE = 0.92
# Rostos com cosseno >= isto pertencem à mesma pessoa (ArcFace ~0.45–0.55 típico).
CLUSTER_COSINE = 0.50


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosseno de dois vetores já normalizados."""
    return float(np.dot(a, b))


@dataclass
class DetectedFace:
    bbox: list[float]  # [x, y, w, h] em pixels
    det_score: float
    embedding: np.ndarray  # float32 512-d, normalizado
    thumbnail: bytes  # recorte JPEG do rosto
    frame_time: float | None = None


# ---------------------------------------------------------------------------
# Detector (seam) — default InsightFace, injetável nos testes
# ---------------------------------------------------------------------------
class FaceDetector(Protocol):
    def detect(self, image: Image.Image) -> list[DetectedFace]: ...


class InsightFaceDetector:
    """Detector padrão: SCRFD (detecção) + ArcFace (embedding) do pacote ``buffalo_l``.

    Carregado preguiçosamente — os modelos (~300 MB) só baixam/abrem no primeiro uso.
    """

    def __init__(self, device: str | None = None, det_size: int = 640):
        self._app = None
        self._device = device
        self._det_size = det_size

    def _providers(self) -> list[str]:
        try:
            import onnxruntime as ort

            available = set(ort.get_available_providers())
        except Exception:
            available = set()
        if self._device == "cuda" and "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    def _ensure(self):
        if self._app is None:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(name="buffalo_l", providers=self._providers())
            ctx_id = 0 if self._device == "cuda" else -1
            app.prepare(ctx_id=ctx_id, det_size=(self._det_size, self._det_size))
            self._app = app
        return self._app

    def detect(self, image: Image.Image) -> list[DetectedFace]:
        app = self._ensure()
        rgb = image.convert("RGB")
        # InsightFace espera BGR (convenção OpenCV).
        bgr = np.array(rgb)[:, :, ::-1]
        out: list[DetectedFace] = []
        for face in app.get(bgr):
            score = float(getattr(face, "det_score", 0.0))
            if score < MIN_DET_SCORE:
                continue
            emb = _normalize(np.asarray(face.normed_embedding, dtype=np.float32))
            x1, y1, x2, y2 = (float(v) for v in face.bbox)
            out.append(
                DetectedFace(
                    bbox=[x1, y1, x2 - x1, y2 - y1],
                    det_score=score,
                    embedding=emb,
                    thumbnail=_crop_thumb(rgb, x1, y1, x2, y2),
                )
            )
        return out


_detector: FaceDetector | None = None


def get_detector(device: str | None = None) -> FaceDetector:
    """Singleton do detector padrão (lazy)."""
    global _detector
    if _detector is None:
        _detector = InsightFaceDetector(device=device)
    return _detector


def set_detector(detector: FaceDetector | None) -> None:
    """Seam de teste: injeta um detector fake (ou ``None`` para resetar)."""
    global _detector
    _detector = detector


def _crop_thumb(rgb: Image.Image, x1: float, y1: float, x2: float, y2: float, pad: float = 0.25) -> bytes:
    w, h = rgb.size
    bw, bh = x2 - x1, y2 - y1
    cx1 = max(0, int(x1 - pad * bw))
    cy1 = max(0, int(y1 - pad * bh))
    cx2 = min(w, int(x2 + pad * bw))
    cy2 = min(h, int(y2 + pad * bh))
    if cx2 <= cx1 or cy2 <= cy1:
        return make_thumbnail(rgb, size=128)
    return make_thumbnail(rgb.crop((cx1, cy1, cx2, cy2)), size=128)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def create_face_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS persons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            cover_face_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meme_id INTEGER NOT NULL,
            person_id INTEGER,
            bbox TEXT NOT NULL DEFAULT '',
            det_score REAL NOT NULL DEFAULT 0,
            frame_time REAL,
            embedding BLOB NOT NULL,
            thumbnail BLOB,
            created_at TEXT NOT NULL,
            FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE,
            FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_meme ON faces(meme_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id)")


def has_face_tables(conn: sqlite3.Connection) -> bool:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return "faces" in tables and "persons" in tables


# ---------------------------------------------------------------------------
# Extração
# ---------------------------------------------------------------------------
def _dedup_faces(faces: list[DetectedFace]) -> list[DetectedFace]:
    """Remove rostos quase idênticos da mesma mídia (ex.: mesmo rosto em 6 frames de vídeo)."""
    kept: list[DetectedFace] = []
    for face in sorted(faces, key=lambda f: f.det_score, reverse=True):
        if all(_cosine(face.embedding, k.embedding) < SAME_FACE_COSINE for k in kept):
            kept.append(face)
    return kept


def extract_faces_for_record(
    conn: sqlite3.Connection,
    meme_id: int,
    images: list[Image.Image],
    *,
    frame_times: list[float | None] | None = None,
    detector: FaceDetector | None = None,
    device: str | None = None,
    replace: bool = True,
) -> int:
    """Detecta rostos em 1+ imagens (vídeo = vários frames), deduplica e grava em ``faces``.

    Função única usada tanto no indexer quanto no backfill. Retorna o nº de rostos gravados.
    """
    detector = detector or get_detector(device)
    detected: list[DetectedFace] = []
    for i, img in enumerate(images):
        ft = frame_times[i] if frame_times is not None and i < len(frame_times) else None
        for face in detector.detect(img):
            face.frame_time = ft
            detected.append(face)

    unique = _dedup_faces(detected)
    if replace:
        conn.execute("DELETE FROM faces WHERE meme_id = ?", (meme_id,))
    now = _now_iso()
    conn.executemany(
        "INSERT INTO faces (meme_id, person_id, bbox, det_score, frame_time, embedding, thumbnail, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                meme_id,
                None,
                json.dumps(f.bbox),
                f.det_score,
                f.frame_time,
                f.embedding.astype(np.float32).tobytes(),
                f.thumbnail,
                now,
            )
            for f in unique
        ],
    )
    conn.commit()
    return len(unique)


# ---------------------------------------------------------------------------
# Agrupamento (clustering) → persons
# ---------------------------------------------------------------------------
def cluster_faces(
    conn: sqlite3.Connection, threshold: float = CLUSTER_COSINE, recluster: bool = False
) -> dict[str, int]:
    """Agrupa rostos por similaridade (vizinho-de-centróide) preenchendo ``faces.person_id``.

    Incremental por padrão: rostos sem pessoa casam no centróide de pessoas existentes ou
    formam pessoas novas; nomes já dados são preservados. ``recluster=True`` refaz do zero.
    """
    conn.row_factory = sqlite3.Row
    if recluster:
        conn.execute("UPDATE faces SET person_id = NULL")
        conn.execute("DELETE FROM persons")
        conn.commit()

    rows = conn.execute(
        "SELECT id, embedding, person_id, det_score FROM faces ORDER BY det_score DESC"
    ).fetchall()

    # Centróides incrementais: person_id -> [soma_vetorial, contagem]
    centroids: dict[int, list[Any]] = {}
    for r in rows:
        pid = r["person_id"]
        if pid is None:
            continue
        emb = np.frombuffer(r["embedding"], dtype=np.float32)
        slot = centroids.setdefault(pid, [np.zeros(emb.shape, dtype=np.float32), 0])
        slot[0] += emb
        slot[1] += 1

    now = _now_iso()
    assignments: list[tuple[int, int]] = []
    for r in rows:
        if r["person_id"] is not None:
            continue
        emb = np.frombuffer(r["embedding"], dtype=np.float32)
        best_pid, best_sim = None, -1.0
        for pid, (vsum, _count) in centroids.items():
            cen = vsum / max(float(np.linalg.norm(vsum)), 1e-8)
            sim = float(np.dot(emb, cen))
            if sim > best_sim:
                best_pid, best_sim = pid, sim
        if best_pid is not None and best_sim >= threshold:
            pid = best_pid
        else:
            cur = conn.execute(
                "INSERT INTO persons (name, cover_face_id, created_at, updated_at) VALUES (NULL, ?, ?, ?)",
                (r["id"], now, now),
            )
            pid = int(cur.lastrowid)
            centroids[pid] = [np.zeros(emb.shape, dtype=np.float32), 0]
        centroids[pid][0] += emb
        centroids[pid][1] += 1
        assignments.append((r["id"], pid))

    conn.executemany(
        "UPDATE faces SET person_id = ? WHERE id = ?", [(p, f) for f, p in assignments]
    )
    _refresh_persons(conn)
    conn.commit()
    return {"persons": _count_persons(conn), "assigned": len(assignments)}


def _refresh_persons(conn: sqlite3.Connection) -> None:
    """Atualiza a capa (rosto de maior score) de cada pessoa e remove pessoas sem rostos."""
    for (pid,) in conn.execute("SELECT id FROM persons").fetchall():
        row = conn.execute(
            "SELECT id FROM faces WHERE person_id = ? ORDER BY det_score DESC LIMIT 1", (pid,)
        ).fetchone()
        if row is None:
            conn.execute("DELETE FROM persons WHERE id = ?", (pid,))
        else:
            conn.execute("UPDATE persons SET cover_face_id = ? WHERE id = ?", (row[0], pid))


def _count_persons(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0])


# ---------------------------------------------------------------------------
# Consultas / CRUD de pessoas
# ---------------------------------------------------------------------------
def list_persons(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not has_face_tables(conn):
        return []
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.cover_face_id,
               COUNT(DISTINCT f.meme_id) AS media_count,
               COUNT(f.id) AS face_count
        FROM persons p
        LEFT JOIN faces f ON f.person_id = p.id
        GROUP BY p.id
        ORDER BY media_count DESC, p.id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_person_meme_ids(conn: sqlite3.Connection, person_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT meme_id FROM faces WHERE person_id = ?", (person_id,)
    ).fetchall()
    return [r[0] for r in rows]


def rename_person(conn: sqlite3.Connection, person_id: int, name: str) -> None:
    clean = name.strip() or None
    conn.execute(
        "UPDATE persons SET name = ?, updated_at = ? WHERE id = ?", (clean, _now_iso(), person_id)
    )
    conn.commit()


def merge_persons(conn: sqlite3.Connection, source_id: int, target_id: int) -> None:
    if source_id == target_id:
        return
    conn.execute("UPDATE faces SET person_id = ? WHERE person_id = ?", (target_id, source_id))
    conn.execute("DELETE FROM persons WHERE id = ?", (source_id,))
    conn.execute("UPDATE persons SET updated_at = ? WHERE id = ?", (_now_iso(), target_id))
    _refresh_persons(conn)
    conn.commit()


def delete_person(conn: sqlite3.Connection, person_id: int) -> None:
    """Remove a pessoa e desvincula seus rostos (os rostos continuam no catálogo)."""
    conn.execute("UPDATE faces SET person_id = NULL WHERE person_id = ?", (person_id,))
    conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
    conn.commit()


def get_media_faces(conn: sqlite3.Connection, meme_id: int) -> list[dict[str, Any]]:
    if not has_face_tables(conn):
        return []
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, person_id, bbox, det_score, frame_time FROM faces WHERE meme_id = ? ORDER BY det_score DESC",
        (meme_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_face_thumbnail(conn: sqlite3.Connection, face_id: int) -> bytes | None:
    row = conn.execute("SELECT thumbnail FROM faces WHERE id = ?", (face_id,)).fetchone()
    if not row:
        return None
    return row[0]


def embed_query_face(
    image: Image.Image, detector: FaceDetector | None = None, device: str | None = None
) -> np.ndarray | None:
    """Embedding do rosto principal (maior área) de uma foto de consulta. ``None`` se não houver."""
    detector = detector or get_detector(device)
    faces = detector.detect(image)
    if not faces:
        return None
    best = max(faces, key=lambda f: f.bbox[2] * f.bbox[3])
    return best.embedding
