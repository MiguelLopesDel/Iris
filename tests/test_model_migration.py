"""Moving a library to another embedding model while search keeps working.

The new vectors pile up beside the catalogue; search stays on the old model
until every item has them, then the library switches in one step. Faces and
taxonomy someone already has are never undone by the reprocessing.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from core import ai_jobs, model_migration
from core.indexer_db import init_db

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

import iris_ai_worker  # noqa: E402
import iris_sync  # noqa: E402

CLIP = "sentence-transformers/clip-ViT-L-14"
SIGLIP = "google/siglip2-base-patch16-224"
DIM = 768


def _blob(seed: int) -> bytes:
    return np.random.default_rng(seed).standard_normal(DIM).astype(np.float32).tobytes()


def _result(seed: int, **overrides) -> dict:
    payload = {
        "model_name": SIGLIP, "embedding_dim": DIM,
        "embedding": base64.b64encode(_blob(seed)).decode(),
        "desc_embedding": base64.b64encode(_blob(seed + 50)).decode(),
        "texto_extraido": "leitura nova", "tags": "tags novas",
        "faces": [{"bbox": "[0,0,80,80]", "det_score": 0.9,
                   "embedding": base64.b64encode(np.ones(512, np.float32).tobytes()).decode()}],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def library(tmp_path: Path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setenv("IRIS_MODEL_NEXT", SIGLIP)
    monkeypatch.delenv("IRIS_MODEL", raising=False)
    conn = init_db(tmp_path / "library.db")
    # Two items already processed with CLIP (one with a named person), one without AI.
    for i in range(2):
        conn.execute(
            "INSERT INTO memes (arquivo, file_mtime, content_hash, model_name, embedding_dim, embedding,"
            " texto_extraido, tags) VALUES (?, ?, ?, ?, ?, ?, 'leitura velha', 'tags velhas')",
            (f"IMG_{i}.jpg", 1.7e9 + i, f"{i:064x}", CLIP, DIM, _blob(i)),
        )
    conn.execute("INSERT INTO memes (arquivo, file_mtime, content_hash) VALUES ('IMG_2.jpg', 1.7e9, ?)",
                 (f"{2:064x}",))
    conn.execute("INSERT INTO persons (name, created_at, updated_at) VALUES ('Alice', '', '')")
    conn.execute(
        "INSERT INTO faces (meme_id, person_id, bbox, det_score, embedding, created_at) VALUES (1, 1, '', 1, ?, '')",
        (np.zeros(512, np.float32).tobytes(),),
    )
    conn.commit()
    return conn


def test_while_moving_every_item_is_offered_including_processed_ones(library):
    target = model_migration.migration_target(library_path(library), CLIP)

    jobs = ai_jobs.claim(library, "pc", 10, target=target)

    assert target == SIGLIP
    assert len(jobs) == 3


def test_new_vectors_wait_beside_the_catalogue_until_the_library_switches(library):
    jobs = ai_jobs.claim(library, "pc", 10, target=SIGLIP)
    first = next(j for j in jobs if j.filename == "IMG_0.jpg")

    ai_jobs.store_result(library, first.media_id, "pc", SIGLIP, _result(1), migrating=True)

    row = library.execute("SELECT model_name, embedding, texto_extraido, tags FROM memes WHERE id = ?",
                          (first.media_id,)).fetchone()
    # Search still has the CLIP vector; the text reading is refreshed, the taxonomy kept.
    assert (row[0], bytes(row[1]), row[2], row[3]) == (CLIP, _blob(0), "leitura nova", "tags velhas")
    assert library.execute("SELECT COUNT(*) FROM embedding_next").fetchone()[0] == 1
    # The named person's face was not replaced.
    faces = library.execute("SELECT person_id FROM faces WHERE meme_id = ?", (first.media_id,)).fetchall()
    assert [f[0] for f in faces] == [1]
    assert model_migration.complete_if_ready(library, SIGLIP) is False


def test_the_library_switches_once_every_item_has_new_vectors(library):
    for job in ai_jobs.claim(library, "pc", 10, target=SIGLIP):
        ai_jobs.store_result(library, job.media_id, "pc", SIGLIP, _result(job.media_id), migrating=True)

    assert model_migration.complete_if_ready(library, SIGLIP) is True

    models = {r[0] for r in library.execute("SELECT model_name FROM memes")}
    assert models == {SIGLIP}
    assert library.execute("SELECT COUNT(*) FROM embedding_next").fetchone()[0] == 0
    assert model_migration.library_model(library_path(library), CLIP) == SIGLIP
    assert model_migration.migration_target(library_path(library), CLIP) == ""
    # The item that had no AI got everything, faces included.
    new = library.execute("SELECT tags FROM memes WHERE arquivo = 'IMG_2.jpg'").fetchone()[0]
    assert new == "tags novas"
    assert library.execute("SELECT COUNT(*) FROM faces WHERE meme_id = 3").fetchone()[0] == 1


def test_vectors_of_another_model_are_refused_while_moving(library):
    job = ai_jobs.claim(library, "pc", 1, target=SIGLIP)[0]

    with pytest.raises(ai_jobs.AiJobError) as refused:
        ai_jobs.store_result(library, job.media_id, "pc", SIGLIP, _result(1, model_name=CLIP), migrating=True)

    assert refused.value.status == 409


def test_without_a_target_nothing_moves(library, monkeypatch):
    monkeypatch.delenv("IRIS_MODEL_NEXT")

    assert model_migration.migration_target(library_path(library), CLIP) == ""
    assert model_migration.library_model(library_path(library), CLIP) == CLIP
    assert [j.filename for j in ai_jobs.claim(library, "pc", 10)] == ["IMG_2.jpg"]


def library_path(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2]


# ── Round trip against a real server ────────────────────────────────────────

PASSWORD = "synthetic password 1"


@pytest.fixture
def server(tmp_path: Path):
    import socket

    home = tmp_path / "server"
    home.mkdir()
    env = dict(
        os.environ, PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false", IRIS_LOAD_MODEL="0", IRIS_THUMBNAIL_WARMUP="0",
        IRIS_BACKEND_PRELOAD="0", IRIS_MODEL=CLIP, IRIS_MODEL_NEXT=SIGLIP,
    )
    subprocess.run(
        [sys.executable, "-c", (
            "import hashlib, sqlite3\n"
            "import numpy as np\n"
            "from pathlib import Path\n"
            "from PIL import Image\n"
            "from core.auth import hash_password\n"
            "from core.indexer_db import init_db\n"
            "from core.users_db import create_user\n"
            f"ana = create_user(Path('data/users.db'), Path('data'), username='ana', is_admin=True,"
            f" password_hash=hash_password({PASSWORD!r}))\n"
            "init_db(ana.db_path).close()\n"
            "ana.media_root.mkdir(parents=True, exist_ok=True)\n"
            "with sqlite3.connect(ana.db_path) as conn:\n"
            "    for i in range(4):\n"
            "        path = ana.media_root / f'IMG_{i:04}.jpg'\n"
            "        Image.new('RGB', (16, 16), (i * 50, 80, 160)).save(path)\n"
            "        digest = hashlib.sha256(path.read_bytes()).hexdigest()\n"
            "        vector = np.random.default_rng(i).standard_normal(768).astype(np.float32).tobytes()\n"
            "        conn.execute('INSERT INTO memes (arquivo, caminho, file_mtime, content_hash, model_name,'\n"
            "                     ' embedding_dim, embedding) VALUES (?, ?, ?, ?, ?, 768, ?)',\n"
            f"                     (path.name, str(path), 1.7e9 + i, digest, {CLIP!r}, vector))\n"
        )],
        cwd=home, env=env, check=True, timeout=60,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--app-dir", str(ROOT),
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=home, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("server did not start")
        yield base, home / "data"
    finally:
        process.terminate()
        process.wait(timeout=20)


def test_a_worker_moves_a_library_to_the_new_model(server, tmp_path, monkeypatch):
    base, data = server
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    config = iris_sync.login(base, "ana", PASSWORD, "PC (IA)", iris_ai_worker.WorkerConfig)
    client = iris_sync.Client(config)
    status, body = client.request("GET", "/api/ai-jobs/status")
    assert body["model_name"] == SIGLIP
    assert body["migration"] == {"target": SIGLIP, "done": 0, "total": 4}

    def fake_pipeline(files, model_name, _workdir):
        assert model_name == SIGLIP
        return {path.name: _result(int(path.stem)) for path in files}

    totals = iris_ai_worker.work_once(client, batch=3, process=fake_pipeline, log=lambda _m: None)

    assert totals == {"done": 4, "failed": 0}
    db = next((data / "users").glob("*/*.db"))
    with sqlite3.connect(db) as conn:
        assert {r[0] for r in conn.execute("SELECT model_name FROM memes")} == {SIGLIP}
    status, body = client.request("GET", "/api/ai-jobs/status")
    assert "migration" not in body and body["pending"] == 0
    # The library is now searched with the new model.
    cookie = _web_login(base)
    info = json.load(urllib.request.urlopen(urllib.request.Request(base + "/api/info", headers={"Cookie": cookie})))
    assert info["model_name"] == SIGLIP


def _web_login(base: str) -> str:
    data = urllib.parse.urlencode({"username": "ana", "password": PASSWORD}).encode()
    response = urllib.request.urlopen(urllib.request.Request(base + "/api/auth/login", data=data))
    return response.headers["Set-Cookie"].split(";")[0]


# ── A trial batch: choosing what to process, and judging it ────────────────


def test_only_new_takes_items_without_any_ai_first(library):
    jobs = ai_jobs.claim(library, "pc", 10, target=SIGLIP, only="new")

    assert [j.filename for j in jobs] == ["IMG_2.jpg"]
    assert ai_jobs.pending_count(library, SIGLIP, "new") == 1
    assert ai_jobs.pending_count(library, SIGLIP, "processed") == 2
    assert ai_jobs.pending_count(library, SIGLIP) == 3


def test_processed_takes_items_that_can_be_compared_with_the_current_model(library):
    jobs = ai_jobs.claim(library, "pc", 10, target=SIGLIP, only="processed")

    assert sorted(j.filename for j in jobs) == ["IMG_0.jpg", "IMG_1.jpg"]


def test_the_comparison_ranks_the_processed_items_with_both_models(library):
    from core import ai_preview

    for job in ai_jobs.claim(library, "pc", 10, target=SIGLIP):
        ai_jobs.store_result(library, job.media_id, "pc", SIGLIP, _result(job.media_id), migrating=True)
    new_query = np.frombuffer(_blob(2), dtype=np.float32)  # item 2's new vector
    current_query = np.frombuffer(_blob(1), dtype=np.float32)  # item 2's CLIP vector (seeded as _blob(1))

    preview = ai_preview.compare(library, SIGLIP, new_query, current_query)

    assert (preview.processed, preview.with_current) == (3, 2)
    assert preview.new[0].media_id == 2
    assert preview.new[0].text == "leitura nova"
    assert preview.current[0].media_id == 2
    # The item that had no AI has no current vector to rank.
    assert {hit.media_id for hit in preview.current} == {1, 2}


class _CountingClient:
    """Stands in for the server: a queue of N items, all processable."""

    def __init__(self, items: int):
        self.left = list(range(1, items + 1))
        self.claims: list[str] = []

    def request(self, method, path, json_body=None):
        if path.startswith("/api/ai-jobs/claim"):
            self.claims.append(path)
            size = int(path.split("limit=")[1].split("&")[0])
            jobs, self.left = self.left[:size], self.left[size:]
            return 200, {"model_name": SIGLIP, "pending": len(self.left),
                         "jobs": [{"id": i, "filename": f"{i}.jpg", "sha256": ""} for i in jobs]}
        return 200, {"ok": True}

    def download(self, _path, target):
        target.write_bytes(b"x")


def test_a_trial_stops_after_the_requested_number_of_items():
    client = _CountingClient(items=50)

    totals = iris_ai_worker.work_once(
        client, batch=16, limit=20, only="new", log=lambda _m: None,
        process=lambda files, _m, _w: {f.name: _result(1) for f in files},
    )

    assert totals == {"done": 20, "failed": 0}
    assert client.claims == ["/api/ai-jobs/claim?limit=16&only=new",
                             "/api/ai-jobs/claim?limit=4&only=new"]


def _current(seed: int, model: str = CLIP) -> dict:
    return {"model_name": model, "embedding": base64.b64encode(_blob(seed)).decode(),
            "desc_embedding": base64.b64encode(_blob(seed + 1)).decode()}


def test_an_item_without_ai_becomes_searchable_with_the_current_model_right_away(library):
    job = ai_jobs.claim(library, "pc", 10, target=SIGLIP, only="new")[0]

    ai_jobs.store_result(library, job.media_id, "pc", SIGLIP,
                         _result(9, current_vectors=_current(77)), migrating=True, current_model=CLIP)

    row = library.execute("SELECT model_name, embedding, embedding_dim FROM memes WHERE id = ?",
                          (job.media_id,)).fetchone()
    assert (row[0], bytes(row[1]), row[2]) == (CLIP, _blob(77), DIM)
    # The new model's vectors still wait for the switch.
    assert library.execute("SELECT model_name FROM embedding_next WHERE meme_id = ?",
                           (job.media_id,)).fetchone()[0] == SIGLIP


def test_current_vectors_never_replace_what_an_item_already_has(library):
    job = next(j for j in ai_jobs.claim(library, "pc", 10, target=SIGLIP) if j.filename == "IMG_0.jpg")

    ai_jobs.store_result(library, job.media_id, "pc", SIGLIP,
                         _result(9, current_vectors=_current(77)), migrating=True, current_model=CLIP)

    assert bytes(library.execute("SELECT embedding FROM memes WHERE id = ?", (job.media_id,)).fetchone()[0]) == _blob(0)


def test_current_vectors_of_another_model_are_ignored(library):
    job = ai_jobs.claim(library, "pc", 10, target=SIGLIP, only="new")[0]

    ai_jobs.store_result(library, job.media_id, "pc", SIGLIP,
                         _result(9, current_vectors=_current(77, model="other/model")),
                         migrating=True, current_model=CLIP)

    assert library.execute("SELECT embedding FROM memes WHERE id = ?", (job.media_id,)).fetchone()[0] is None
