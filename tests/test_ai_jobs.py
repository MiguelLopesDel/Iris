"""Remote AI processing: leases, what a worker may write, and the round trip.

The worker's own models never run here: a fake pipeline stands in for the
indexer, so these tests pin the contract -- who may work on what, what is
accepted, what reaches the catalogue -- without downloading anything.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from core import ai_jobs
from core.indexer_db import init_db

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop"))

import iris_ai_worker  # noqa: E402
import iris_sync  # noqa: E402

MODEL = "sentence-transformers/clip-ViT-L-14"
DIM = 768
PASSWORD = "synthetic password 1"


def _vec(seed: int, dim: int = DIM) -> str:
    vector = np.random.default_rng(seed).standard_normal(dim).astype(np.float32)
    return base64.b64encode(vector.tobytes()).decode()


def _result(seed: int = 1, **overrides) -> dict:
    payload = {
        "model_name": MODEL, "embedding_dim": DIM, "embedding": _vec(seed),
        "desc_embedding": _vec(seed + 100), "texto_extraido": "CARTEIRA DE IDENTIDADE",
        "descricao_ia": "documento", "tags": "documento", "width": 640, "height": 480,
        "faces": [{"bbox": "[0,0,80,80]", "det_score": 0.9, "frame_time": None,
                   "embedding": _vec(seed + 200, 512), "thumbnail": ""}],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def library(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(tmp_path / "library.db")
    for i in range(3):
        conn.execute(
            "INSERT INTO memes (arquivo, caminho, file_mtime, content_hash) VALUES (?, ?, ?, ?)",
            (f"IMG_{i:04}.jpg", f"/media/IMG_{i:04}.jpg", 1.7e9 + i, f"{i:064x}"),
        )
    conn.commit()
    return conn


def test_a_claim_leases_items_newest_first_to_one_device(library):
    jobs = ai_jobs.claim(library, "pc-1", 2, now=1000)

    assert [job.filename for job in jobs] == ["IMG_0002.jpg", "IMG_0001.jpg"]
    # Another device gets what is left, not the leased ones.
    assert [job.filename for job in ai_jobs.claim(library, "pc-2", 5, now=1001)] == ["IMG_0000.jpg"]
    # The same device, restarted, is offered its own leases again.
    assert len(ai_jobs.claim(library, "pc-1", 5, now=1002)) == 2


def test_an_expired_lease_goes_back_to_the_queue(library):
    ai_jobs.claim(library, "pc-1", 3, now=1000)

    later = 1000 + ai_jobs.LEASE_SECONDS + 1
    assert len(ai_jobs.claim(library, "pc-2", 3, now=later)) == 3


def test_a_file_that_keeps_failing_stops_being_offered(library):
    for _ in range(ai_jobs.MAX_ATTEMPTS):
        job = ai_jobs.claim(library, "pc-1", 1)[0]
        assert job.filename == "IMG_0002.jpg"
        ai_jobs.record_failure(library, job.media_id, "pc-1", "UnidentifiedImageError")

    assert [job.filename for job in ai_jobs.claim(library, "pc-1", 5)] == ["IMG_0001.jpg", "IMG_0000.jpg"]
    assert ai_jobs.pending_count(library) == 2


def test_a_result_writes_only_the_ai_columns_and_the_faces(library):
    job = ai_jobs.claim(library, "pc-1", 1)[0]

    ai_jobs.store_result(library, job.media_id, "pc-1", MODEL,
                         _result(arquivo="renomeado.jpg", caminho="/etc/passwd"))

    row = library.execute(
        "SELECT arquivo, caminho, texto_extraido, embedding_dim, length(embedding), width FROM memes WHERE id = ?",
        (job.media_id,),
    ).fetchone()
    assert tuple(row) == ("IMG_0002.jpg", "/media/IMG_0002.jpg", "CARTEIRA DE IDENTIDADE", DIM, DIM * 4, 640)
    assert library.execute("SELECT COUNT(*) FROM faces WHERE meme_id = ?", (job.media_id,)).fetchone()[0] == 1
    assert ai_jobs.pending_count(library) == 2


def test_unmeasured_dimensions_do_not_erase_known_ones(library):
    library.execute("UPDATE memes SET width = 4000, height = 3000")
    job = ai_jobs.claim(library, "pc-1", 1)[0]

    ai_jobs.store_result(library, job.media_id, "pc-1", MODEL, _result(width=0, height=0))

    row = library.execute("SELECT width, height FROM memes WHERE id = ?", (job.media_id,)).fetchone()
    assert tuple(row) == (4000, 3000)


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        (_result(model_name="other-model"), 409),
        (_result(embedding_dim=512), 400),
        (_result(embedding=base64.b64encode(np.full(DIM, np.nan, np.float32).tobytes()).decode()), 400),
        (_result(embedding="not base64!"), 400),
        (_result(faces=[{"embedding": _vec(1, 128)}]), 400),
    ],
)
def test_a_result_that_does_not_fit_the_library_is_refused(library, payload, status):
    job = ai_jobs.claim(library, "pc-1", 1)[0]

    with pytest.raises(ai_jobs.AiJobError) as refused:
        ai_jobs.store_result(library, job.media_id, "pc-1", MODEL, payload)

    assert refused.value.status == status
    assert library.execute("SELECT embedding FROM memes WHERE id = ?", (job.media_id,)).fetchone()[0] is None


def test_only_the_device_holding_the_lease_may_answer(library):
    job = ai_jobs.claim(library, "pc-1", 1)[0]

    with pytest.raises(ai_jobs.AiJobError) as refused:
        ai_jobs.store_result(library, job.media_id, "pc-2", MODEL, _result())

    assert refused.value.status == 409


def test_a_result_for_another_file_is_refused(library):
    job = ai_jobs.claim(library, "pc-1", 1)[0]

    with pytest.raises(ai_jobs.AiJobError) as refused:
        ai_jobs.store_result(library, job.media_id, "pc-1", MODEL, _result(sha256="f" * 64))

    assert refused.value.status == 409


def test_rebuilt_indices_keep_one_row_per_item(library, tmp_path):
    import faiss

    job = ai_jobs.claim(library, "pc-1", 1)[0]
    ai_jobs.store_result(library, job.media_id, "pc-1", MODEL, _result())

    ai_jobs.rebuild_vector_indices(tmp_path / "library.db")

    index = faiss.read_index(str(tmp_path / "library_image.faiss"))
    assert index.ntotal == 3  # items still without AI keep their (zero) row


def test_the_worker_reads_what_the_indexer_wrote(tmp_path):
    conn = init_db(tmp_path / "work.db")
    embedding = np.ones(DIM, np.float32).tobytes()
    conn.execute(
        "INSERT INTO memes (arquivo, model_name, embedding, texto_extraido, width) VALUES (?, ?, ?, ?, ?)",
        ("7.jpg", MODEL, embedding, "texto", 10),
    )
    conn.execute(
        "INSERT INTO faces (meme_id, bbox, det_score, embedding, created_at) VALUES (1, '[0,0,80,80]', 0.8, ?, '')",
        (np.ones(512, np.float32).tobytes(),),
    )
    conn.commit()

    results = iris_ai_worker.read_results(tmp_path / "work.db")

    assert set(results) == {"7.jpg"}
    assert results["7.jpg"]["embedding_dim"] == DIM
    assert results["7.jpg"]["texto_extraido"] == "texto"
    assert len(results["7.jpg"]["faces"]) == 1


# ── Round trip against a real server ────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path):
    home = tmp_path / "server"
    home.mkdir()
    env = dict(
        os.environ, PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false", IRIS_LOAD_MODEL="0", IRIS_THUMBNAIL_WARMUP="0",
        IRIS_BACKEND_PRELOAD="0", IRIS_MODEL=MODEL,
    )
    subprocess.run(
        [sys.executable, "-c", (
            "import hashlib, sqlite3\n"
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
            "    for i in range(5):\n"
            "        path = ana.media_root / f'IMG_{i:04}.jpg'\n"
            "        Image.new('RGB', (16, 16), (i * 40, 80, 160)).save(path)\n"
            "        digest = hashlib.sha256(path.read_bytes()).hexdigest()\n"
            "        conn.execute('INSERT INTO memes (arquivo, caminho, file_mtime, content_hash)'\n"
            "                     ' VALUES (?, ?, ?, ?)', (path.name, str(path), 1.7e9 + i, digest))\n"
        )],
        cwd=home, env=env, check=True, timeout=60,
    )
    port = _free_port()
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


def test_a_worker_fills_the_library_and_the_search_indices(server, tmp_path, monkeypatch):
    base, data = server
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    config = iris_sync.login(base, "ana", PASSWORD, "PC de teste (IA)", iris_ai_worker.WorkerConfig)
    client = iris_sync.Client(config)
    seen: list[str] = []

    def fake_pipeline(files: list[Path], model_name: str, _workdir: Path) -> dict[str, dict]:
        assert model_name == MODEL
        results = {}
        for path in files:
            seen.append(hashlib.sha256(path.read_bytes()).hexdigest())
            if path.name.startswith("1."):  # one file the pipeline cannot read
                continue
            results[path.name] = _result(seed=int(path.stem))
        return results

    totals = iris_ai_worker.work_once(client, batch=2, process=fake_pipeline, log=lambda _m: None)

    db = next((data / "users").glob("*/*.db"))
    with sqlite3.connect(db) as conn:
        filled = conn.execute("SELECT COUNT(*) FROM memes WHERE embedding IS NOT NULL").fetchone()[0]
        faces = conn.execute("SELECT COUNT(*) FROM faces WHERE person_id IS NOT NULL").fetchone()[0]
        hashes = {row[0] for row in conn.execute("SELECT content_hash FROM memes")}
    # The unreadable one is retried by later batches until it is given up on.
    assert totals == {"done": 4, "failed": ai_jobs.MAX_ATTEMPTS}
    assert filled == 4
    assert faces == 4  # grouped into people at the end of each batch
    assert set(seen) <= hashes  # the worker got the library's own bytes
    assert list(db.parent.glob("*_image.faiss"))
    status, body = client.request("GET", "/api/ai-jobs/status")
    assert (status, body["pending"]) == (200, 0)


def test_a_web_page_cannot_act_as_a_worker_for_another_account(server):
    base, _data = server
    request = urllib.request.Request(base + "/api/ai-jobs/claim", method="POST")
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=10)
    assert refused.value.code == 401
