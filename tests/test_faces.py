"""Testes do pipeline facial — detecção/extração, agrupamento e busca por pessoa.

O detector InsightFace é substituído por um fake injetado via ``faces.set_detector``,
então nada baixa modelos nem precisa de onnxruntime no CI.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest
from PIL import Image

from core import faces
from core.indexer_db import init_db


def _unit(vec: list[float]) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    return arr / np.linalg.norm(arr)


class FakeDetector:
    """Detector determinístico: mapeia ``id(image)`` → lista de (embedding, bbox, score)."""

    def __init__(self, mapping: dict):
        self.mapping = mapping

    def detect(self, image: Image.Image) -> list[faces.DetectedFace]:
        out = []
        for emb, bbox, score in self.mapping.get(id(image), []):
            out.append(
                faces.DetectedFace(
                    bbox=list(bbox),
                    det_score=score,
                    embedding=faces._normalize(np.asarray(emb, dtype=np.float32)),
                    thumbnail=b"\xff\xd8\xff",  # JPEG mágico mínimo (suficiente p/ testes)
                )
            )
        return out


@pytest.fixture(autouse=True)
def _reset_detector():
    yield
    faces.set_detector(None)


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "faces.db")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _add_meme(conn: sqlite3.Connection, arquivo: str) -> int:
    emb = _unit([1.0, 0.0, 0.0, 0.0]).tobytes()
    cur = conn.execute(
        "INSERT INTO memes (arquivo, caminho, embedding, model_name, schema_version)"
        " VALUES (?,?,?,?,?)",
        (arquivo, f"/{arquivo}", emb, "m", 4),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_schema_tables_created(conn):
    assert faces.has_face_tables(conn)


def test_extract_dedups_same_face_across_frames(conn):
    meme_id = _add_meme(conn, "video.mp4")
    a = _unit([1.0, 0.0, 0.0])
    b = _unit([0.0, 1.0, 0.0])
    img1, img2, img3 = Image.new("RGB", (10, 10)), Image.new("RGB", (10, 10)), Image.new("RGB", (10, 10))
    # img1/img2 trazem o MESMO rosto (a); img3 traz outro rosto (b).
    faces.set_detector(
        FakeDetector(
            {
                id(img1): [(a, (0, 0, 5, 5), 0.99)],
                id(img2): [(a, (1, 1, 5, 5), 0.95)],
                id(img3): [(b, (0, 0, 5, 5), 0.90)],
            }
        )
    )
    n = faces.extract_faces_for_record(
        conn, meme_id, [img1, img2, img3], frame_times=[0.0, 1.0, 2.0]
    )
    assert n == 2  # o rosto repetido foi deduplicado
    rows = conn.execute("SELECT COUNT(*) FROM faces WHERE meme_id = ?", (meme_id,)).fetchone()[0]
    assert rows == 2


def test_cluster_groups_into_persons(conn):
    # Dois clusters bem separados → duas pessoas.
    p1a, p1b = _unit([1.0, 0.0, 0.0]), _unit([0.97, 0.05, 0.0])
    p2a, p2b = _unit([0.0, 1.0, 0.0]), _unit([0.02, 0.99, 0.0])
    for emb in (p1a, p1b, p2a, p2b):
        mid = _add_meme(conn, "x.jpg")
        conn.execute(
            "INSERT INTO faces (meme_id, bbox, det_score, embedding, created_at)"
            " VALUES (?,?,?,?,?)",
            (mid, "[0,0,5,5]", 0.9, emb.astype(np.float32).tobytes(), "now"),
        )
    conn.commit()

    stats = faces.cluster_faces(conn, threshold=0.5)
    assert stats["persons"] == 2
    assert stats["assigned"] == 4
    persons = faces.list_persons(conn)
    assert len(persons) == 2
    assert all(p["face_count"] == 2 for p in persons)


def test_cluster_is_incremental(conn):
    base = _unit([1.0, 0.0, 0.0])
    mid = _add_meme(conn, "a.jpg")
    conn.execute(
        "INSERT INTO faces (meme_id, bbox, det_score, embedding, created_at) VALUES (?,?,?,?,?)",
        (mid, "[0,0,5,5]", 0.9, base.astype(np.float32).tobytes(), "now"),
    )
    conn.commit()
    faces.cluster_faces(conn, threshold=0.5)
    assert faces._count_persons(conn) == 1

    # Novo rosto parecido entra na MESMA pessoa (sem criar outra).
    near = _unit([0.96, 0.08, 0.0])
    mid2 = _add_meme(conn, "b.jpg")
    conn.execute(
        "INSERT INTO faces (meme_id, bbox, det_score, embedding, created_at) VALUES (?,?,?,?,?)",
        (mid2, "[0,0,5,5]", 0.9, near.astype(np.float32).tobytes(), "now"),
    )
    conn.commit()
    faces.cluster_faces(conn, threshold=0.5)
    assert faces._count_persons(conn) == 1


def test_rename_merge_delete_person(conn):
    for emb in (_unit([1, 0, 0]), _unit([0, 1, 0])):
        mid = _add_meme(conn, "x.jpg")
        conn.execute(
            "INSERT INTO faces (meme_id, bbox, det_score, embedding, created_at) VALUES (?,?,?,?,?)",
            (mid, "[0,0,5,5]", 0.9, emb.astype(np.float32).tobytes(), "now"),
        )
    conn.commit()
    faces.cluster_faces(conn, threshold=0.5)
    persons = faces.list_persons(conn)
    assert len(persons) == 2

    faces.rename_person(conn, persons[0]["id"], "Alice")
    assert any(p["name"] == "Alice" for p in faces.list_persons(conn))

    faces.merge_persons(conn, persons[1]["id"], persons[0]["id"])
    assert faces._count_persons(conn) == 1

    faces.delete_person(conn, persons[0]["id"])
    assert faces._count_persons(conn) == 0
    # Os rostos continuam no catálogo, apenas sem pessoa.
    assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 2


def test_embed_query_face_picks_largest(conn):
    big = _unit([1.0, 0.0, 0.0])
    small = _unit([0.0, 1.0, 0.0])
    img = Image.new("RGB", (10, 10))
    faces.set_detector(
        FakeDetector(
            {
                id(img): [
                    (small, (0, 0, 2, 2), 0.99),  # área 4
                    (big, (0, 0, 8, 8), 0.80),    # área 64 (maior)
                ]
            }
        )
    )
    emb = faces.embed_query_face(img)
    assert emb is not None
    assert float(np.dot(emb, big)) > 0.99


def test_embed_query_face_none_when_no_face(conn):
    img = Image.new("RGB", (10, 10))
    faces.set_detector(FakeDetector({}))
    assert faces.embed_query_face(img) is None


def test_search_face_aggregates_by_media(tmp_path):
    from core.search_engine import IrisEngine

    db = tmp_path / "search.db"
    conn = init_db(db)
    target = _unit([1.0, 0.0, 0.0])
    other = _unit([0.0, 1.0, 0.0])
    # meme 1: contém o alvo (2 rostos); meme 2: só outra pessoa.
    m1 = _add_meme(conn, "alvo.jpg")
    m2 = _add_meme(conn, "outro.jpg")
    for mid, emb in [(m1, target), (m1, other), (m2, other)]:
        conn.execute(
            "INSERT INTO faces (meme_id, bbox, det_score, embedding, created_at) VALUES (?,?,?,?,?)",
            (mid, "[0,0,5,5]", 0.9, emb.astype(np.float32).tobytes(), "now"),
        )
    conn.commit()
    conn.close()

    engine = IrisEngine(db_path=str(db), load_model=False)
    results = engine.search_face(target, top_k=10)
    assert results, "deveria encontrar a mídia com o alvo"
    assert results[0].index == 0  # meme 1 (primeiro registro) tem o melhor rosto
    assert results[0].score > 0.99


def test_search_face_by_record_uses_gallery_item(tmp_path):
    from core.search_engine import IrisEngine

    db = tmp_path / "byrecord.db"
    conn = init_db(db)
    target = _unit([1.0, 0.0, 0.0])
    other = _unit([0.0, 1.0, 0.0])
    m1 = _add_meme(conn, "ref.jpg")     # referência: contém o alvo
    m2 = _add_meme(conn, "match.jpg")   # outra mídia com o alvo
    m3 = _add_meme(conn, "nao.jpg")     # pessoa diferente
    for mid, emb in [(m1, target), (m2, target), (m3, other)]:
        conn.execute(
            "INSERT INTO faces (meme_id, bbox, det_score, embedding, created_at) VALUES (?,?,?,?,?)",
            (mid, "[0,0,5,5]", 0.9, emb.astype(np.float32).tobytes(), "now"),
        )
    conn.commit()
    conn.close()

    engine = IrisEngine(db_path=str(db), load_model=False)
    # Usa o item 0 (ref.jpg) da galeria como referência → acha ref + match (não "nao").
    results = engine.search_face_by_record(0, top_k=10)
    assert results is not None
    found = {r.index for r in results}
    assert {0, 1} <= found
    assert 2 not in found


def test_search_face_by_record_none_without_face(tmp_path):
    from core.search_engine import IrisEngine

    db = tmp_path / "noface.db"
    conn = init_db(db)
    _add_meme(conn, "sem_rosto.jpg")  # nenhum rosto inserido
    conn.commit()
    conn.close()

    engine = IrisEngine(db_path=str(db), load_model=False)
    assert engine.search_face_by_record(0) is None


def _add_face(conn: sqlite3.Connection, meme_id: int, emb: np.ndarray, det_score: float = 0.9) -> int:
    cur = conn.execute(
        "INSERT INTO faces (meme_id, bbox, det_score, embedding, created_at) VALUES (?,?,?,?,?)",
        (meme_id, "[0,0,5,5]", det_score, emb.astype(np.float32).tobytes(), "now"),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_set_face_person_assign_and_unassign(conn):
    mid = _add_meme(conn, "x.jpg")
    fid = _add_face(conn, mid, _unit([1, 0, 0]))
    pid = faces.create_person(conn, "Alice")

    faces.set_face_person(conn, fid, pid)
    assert conn.execute("SELECT person_id FROM faces WHERE id = ?", (fid,)).fetchone()[0] == pid
    # Cover was refreshed to the assigned face.
    assert conn.execute("SELECT cover_face_id FROM persons WHERE id = ?", (pid,)).fetchone()[0] == fid

    # Unassign → the now-empty person is garbage-collected (same as merge/cluster).
    faces.set_face_person(conn, fid, None)
    assert conn.execute("SELECT person_id FROM faces WHERE id = ?", (fid,)).fetchone()[0] is None
    assert faces._count_persons(conn) == 0


def test_set_face_person_reassign_moves_between_persons(conn):
    m1, m2 = _add_meme(conn, "a.jpg"), _add_meme(conn, "b.jpg")
    f1 = _add_face(conn, m1, _unit([1, 0, 0]))
    f2 = _add_face(conn, m2, _unit([0, 1, 0]))
    faces.cluster_faces(conn, threshold=0.5)
    persons = faces.list_persons(conn)
    assert len(persons) == 2
    target = next(p["id"] for p in persons if p["cover_face_id"] == f1)

    faces.set_face_person(conn, f2, target)
    assert faces._count_persons(conn) == 1  # the emptied person was removed
    remaining = faces.list_persons(conn)[0]
    assert remaining["id"] == target
    assert remaining["face_count"] == 2


def test_set_face_person_validates_ids(conn):
    with pytest.raises(ValueError):
        faces.set_face_person(conn, 999, None)
    mid = _add_meme(conn, "x.jpg")
    fid = _add_face(conn, mid, _unit([1, 0, 0]))
    with pytest.raises(ValueError):
        faces.set_face_person(conn, fid, 999)


def test_create_person_trims_name(conn):
    pid = faces.create_person(conn, "  Bia  ")
    assert conn.execute("SELECT name FROM persons WHERE id = ?", (pid,)).fetchone()[0] == "Bia"
    anon = faces.create_person(conn, "   ")
    assert conn.execute("SELECT name FROM persons WHERE id = ?", (anon,)).fetchone()[0] is None


def test_get_media_persons_bulk(conn):
    m1, m2, m3 = _add_meme(conn, "a.jpg"), _add_meme(conn, "b.jpg"), _add_meme(conn, "c.jpg")
    # Assign right after creating: empty persons are GC'd on the next assignment.
    alice = faces.create_person(conn, "Alice")
    faces.set_face_person(conn, _add_face(conn, m1, _unit([1, 0, 0])), alice)
    bob = faces.create_person(conn, "Bob")
    faces.set_face_person(conn, _add_face(conn, m1, _unit([0, 1, 0])), bob)
    faces.set_face_person(conn, _add_face(conn, m2, _unit([0.9, 0.1, 0])), alice)
    _add_face(conn, m3, _unit([0, 0, 1]))  # face without person → not listed

    mapping = faces.get_media_persons(conn, [m1, m2, m3, 12345])
    assert [p["name"] for p in mapping[m1]] == ["Alice", "Bob"]
    assert [p["name"] for p in mapping[m2]] == ["Alice"]
    assert m3 not in mapping
    assert 12345 not in mapping
    assert faces.get_media_persons(conn, []) == {}


def test_get_person_media(tmp_path):
    from core.search_engine import IrisEngine

    db = tmp_path / "person.db"
    conn = init_db(db)
    a = _unit([1.0, 0.0, 0.0])
    m1 = _add_meme(conn, "a.jpg")
    conn.execute(
        "INSERT INTO faces (meme_id, bbox, det_score, embedding, created_at) VALUES (?,?,?,?,?)",
        (m1, "[0,0,5,5]", 0.9, a.astype(np.float32).tobytes(), "now"),
    )
    conn.commit()
    faces.cluster_faces(conn, threshold=0.5)
    person_id = faces.list_persons(conn)[0]["id"]
    conn.close()

    engine = IrisEngine(db_path=str(db), load_model=False)
    results = engine.get_person_media(person_id)
    assert len(results) == 1
    assert results[0].index == 0
