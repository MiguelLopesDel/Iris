"""First-run web setup: the installation code gates who becomes administrator."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from core import first_setup


def _run(tmp_path: Path, script: str) -> str:
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
        IRIS_LOAD_MODEL="0",
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_the_installation_code_is_stable_private_and_forgiving_to_type(tmp_path: Path) -> None:
    code = first_setup.ensure_setup_code(tmp_path)
    assert code == first_setup.ensure_setup_code(tmp_path), "a restart must not change the code"
    mode = stat.S_IMODE((tmp_path / first_setup.CODE_FILE).stat().st_mode)
    assert mode == 0o600
    assert len(code) == 9 and code[4] == "-"
    assert not set(code.replace("-", "")) & set("01ILO")
    assert first_setup.code_matches(tmp_path, code)
    assert first_setup.code_matches(tmp_path, code.lower().replace("-", " "))
    assert not first_setup.code_matches(tmp_path, "AAAA-AAAA")
    first_setup.clear_setup_code(tmp_path)
    assert not first_setup.code_matches(tmp_path, code)


def test_attempts_are_bounded_within_a_window() -> None:
    now = [0.0]
    limiter = first_setup.AttemptLimiter(max_failures=3, window_seconds=60, clock=lambda: now[0])
    for _ in range(3):
        assert not limiter.blocked()
        limiter.record_failure()
    assert limiter.blocked()
    now[0] = 61.0
    assert not limiter.blocked()


def test_web_setup_creates_the_administrator_once_and_signs_in(tmp_path: Path) -> None:
    out = _run(tmp_path, r'''
from pathlib import Path
from fastapi.testclient import TestClient
import server

data = Path("data")
with TestClient(server.app) as client:
    assert client.get("/healthz").json()["status"] == "setup_required"
    code = (data / "setup_code").read_text().strip()
    assert client.get("/api/setup").json() == {"required": True, "legacy_library": False}
    assert client.get("/setup").status_code == 200
    assert client.get("/api/info").status_code == 503

    base = {"username": "admin", "display_name": "Admin", "password": "senha muito segura"}
    assert client.post("/api/setup", json={**base, "code": "AAAA-AAAA"}).status_code == 403
    assert client.post("/api/setup", json={**base, "code": code, "password": "curta"}).status_code == 422
    assert client.post("/api/setup", json={**base, "code": code, "username": "x"}).status_code == 422

    done = client.post("/api/setup", json={**base, "code": code.lower()})
    assert done.status_code == 201, done.text
    assert done.json()["user"]["is_admin"] is True

    # Signed in, no restart: the library answers and setup is closed for good.
    assert client.get("/api/info").status_code == 200
    assert client.get("/healthz").json()["status"] == "ok"
    assert not (data / "setup_code").exists()
    assert client.get("/api/setup").json() == {"required": False}
    assert client.post("/api/setup", json={**base, "code": code}).status_code == 409
    assert client.get("/setup", follow_redirects=False).status_code == 303
print("ok")
''')
    assert out.strip().endswith("ok")


def test_web_setup_moves_a_legacy_library_into_the_administrator(tmp_path: Path) -> None:
    out = _run(tmp_path, r'''
from pathlib import Path
from fastapi.testclient import TestClient
from core.indexer_db import init_db

data = Path("data")
data.mkdir()
init_db(data / "iris_v1.db").close()
media = Path("media")
media.mkdir()
(media / "foto.jpg").write_bytes(b"jpeg")

import server
with TestClient(server.app) as client:
    assert client.get("/api/setup").json()["legacy_library"] is True
    code = (data / "setup_code").read_text().strip()
    done = client.post("/api/setup", json={
        "code": code, "username": "admin", "password": "senha muito segura",
    })
    assert done.status_code == 201, done.text
    assert done.json()["migrated_legacy_library"] is True

user_root = data / "users" / str(done.json()["user"]["id"])
assert (user_root / "iris.db").is_file()
assert (user_root / "media" / "foto.jpg").read_bytes() == b"jpeg"
assert not (data / "iris_v1.db").exists()
print("ok")
''')
    assert out.strip().endswith("ok")


def test_wrong_codes_are_rate_limited(tmp_path: Path) -> None:
    out = _run(tmp_path, r'''
from fastapi.testclient import TestClient
import server

with TestClient(server.app) as client:
    body = {"code": "AAAA-AAAA", "username": "admin", "password": "senha muito segura"}
    statuses = [client.post("/api/setup", json=body).status_code for _ in range(11)]
    assert statuses[:10] == [403] * 10, statuses
    assert statuses[10] == 429, statuses
print("ok")
''')
    assert out.strip().endswith("ok")


def test_concurrent_submissions_create_a_single_administrator(tmp_path: Path) -> None:
    out = _run(tmp_path, r'''
import asyncio
from pathlib import Path
import httpx
import server
from core import first_setup
from core.users_db import list_users

data = Path("data")
code = first_setup.ensure_setup_code(data)

async def main():
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bodies = [{"code": code, "username": f"admin{i}", "password": "senha muito segura"} for i in range(5)]
        return await asyncio.gather(*(client.post("/api/setup", json=b) for b in bodies))

responses = asyncio.run(main())
statuses = sorted(r.status_code for r in responses)
assert statuses == [201, 409, 409, 409, 409], statuses
assert len(list_users(data / "users.db")) == 1
print("ok")
''')
    assert out.strip().endswith("ok")


def test_terminal_bootstrap_reprompts_a_short_password_instead_of_crashing(tmp_path: Path) -> None:
    args = ["--username", "admin"]
    script = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_admin.py"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run(
        [sys.executable, "-c", (
            "import getpass, runpy, sys\n"
            "answers = iter(['curta', 'senha muito segura', 'senha muito segura'])\n"
            "getpass.getpass = lambda prompt='': next(answers)\n"
            f"sys.argv = ['bootstrap_admin.py', *{args!r}]\n"
            f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
        )],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "pelo menos 8 caracteres" in result.stderr
    assert "Conta administradora criada: admin" in result.stdout


_LEGACY_LIBRARY = r'''
import errno, os, shutil
from pathlib import Path
from fastapi.testclient import TestClient
from core.indexer_db import init_db

data = Path("data")
data.mkdir()
init_db(data / "iris_v1.db").close()
media = Path("media")
media.mkdir()
for name in ("a.jpg", "b.jpg", "c.jpg"):
    (media / name).write_bytes(name.encode() * 1000)

# The media folder on another disk: renames fail and moves fall back to copying.
real_rename = os.rename
def other_disk_for_media(src, dst, *args, **kwargs):
    if "media" in str(src) or "media" in str(dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")
    return real_rename(src, dst, *args, **kwargs)
os.rename = other_disk_for_media
'''


def test_a_migration_that_runs_out_of_space_is_rolled_back_and_can_be_retried(tmp_path: Path) -> None:
    out = _run(tmp_path, _LEGACY_LIBRARY + r'''
real_copy = shutil.copy2
copies = {"n": 0}
def disk_fills_on_the_second_photo(src, dst, *args, **kwargs):
    copies["n"] += 1
    if copies["n"] == 2:
        Path(dst).write_bytes(b"half")  # a partial copy, as a full disk leaves it
        raise OSError(errno.ENOSPC, "No space left on device")
    return real_copy(src, dst, *args, **kwargs)
shutil.copy2 = disk_fills_on_the_second_photo

import server
with TestClient(server.app) as client:
    code = (data / "setup_code").read_text().strip()
    body = {"code": code, "username": "admin", "password": "senha muito segura"}
    failed = client.post("/api/setup", json=body)
    assert failed.status_code == 507, failed.text
    assert "Nada foi alterado" in failed.json()["detail"]
    # As before: no account, the library whole where it was, setup open, same code.
    assert client.get("/healthz").json()["status"] == "setup_required"
    status = client.get("/api/setup").json()
    assert status["required"] is True and status["legacy_library"] is True
    assert (data / "iris_v1.db").is_file()
    assert {p.name: p.read_bytes() for p in media.iterdir()} == {
        n: n.encode() * 1000 for n in ("a.jpg", "b.jpg", "c.jpg")
    }
    assert not (data / "users").exists() or not any((data / "users").iterdir())

    # Space freed: the same code completes setup.
    shutil.copy2 = real_copy
    done = client.post("/api/setup", json=body)
    assert done.status_code == 201, done.text
    assert done.json()["migrated_legacy_library"] is True
user_root = data / "users" / str(done.json()["user"]["id"])
assert {p.name: p.read_bytes() for p in (user_root / "media").iterdir()} == {
    n: n.encode() * 1000 for n in ("a.jpg", "b.jpg", "c.jpg")
}
assert not any(media.iterdir())
print("ok")
''')
    assert out.strip().endswith("ok")


def test_when_putting_back_also_fails_the_account_stays_and_setup_closes(tmp_path: Path) -> None:
    out = _run(tmp_path, _LEGACY_LIBRARY + r'''
real_copy = shutil.copy2
def copies_fail_after_the_first(src, dst, *args, **kwargs):
    # The first photo moves; the second fails; moving the first back fails too.
    if "b.jpg" in str(src) or "users" in str(src):
        raise OSError(errno.EIO, "Input/output error")
    return real_copy(src, dst, *args, **kwargs)
shutil.copy2 = copies_fail_after_the_first

import server
with TestClient(server.app) as client:
    code = (data / "setup_code").read_text().strip()
    failed = client.post("/api/setup", json={"code": code, "username": "admin", "password": "senha muito segura"})
    assert failed.status_code == 500, failed.text
    detail = failed.json()["detail"]
    assert "não pôde ser desfeita" in detail and "a.jpg" in detail
    # The account exists, so the server no longer waits for setup.
    assert client.get("/healthz").json()["status"] == "ok"
    assert not (data / "setup_code").exists()
    login = client.post("/api/auth/login", data={"username": "admin", "password": "senha muito segura"})
    assert login.status_code == 200
# No file was lost: each photo is in exactly one of the two places.
user_media = data / "users" / "1" / "media"
found = sorted([p.name for p in media.iterdir()] + [p.name for p in user_media.iterdir()])
assert found == ["a.jpg", "b.jpg", "c.jpg"], found
print("ok")
''')
    assert out.strip().endswith("ok")


def test_a_library_that_will_not_fit_is_refused_before_anything_changes(tmp_path: Path, monkeypatch) -> None:
    from core.indexer_db import init_db

    data = tmp_path / "data"
    data.mkdir()
    init_db(data / "iris_v1.db").close()
    media = tmp_path / "media"
    media.mkdir()
    (media / "big.mp4").write_bytes(b"x" * 4096)
    monkeypatch.setattr(first_setup, "_device_of", lambda path: 2 if "media" in str(path) else 1)
    monkeypatch.setattr(first_setup.shutil, "disk_usage", lambda path: shutil_usage(free=1024))

    legacy = first_setup.find_legacy_library(data, media)
    try:
        first_setup.create_first_admin(data / "users.db", data, legacy, username="admin", password_hash="x")
    except first_setup.SetupError as exc:
        assert "Espaço insuficiente" in str(exc)
    else:
        raise AssertionError("a library that does not fit must be refused")
    from core.users_db import has_users

    assert not has_users(data / "users.db")
    assert (media / "big.mp4").is_file() and (data / "iris_v1.db").is_file()


def shutil_usage(free: int):
    import collections

    return collections.namedtuple("usage", "total used free")(free * 10, free * 9, free)


def test_a_source_folder_that_is_only_partly_removed_never_loses_files(tmp_path: Path) -> None:
    out = _run(tmp_path, _LEGACY_LIBRARY + r'''
trip = media / "viagem"
trip.mkdir()
for name in ("1.jpg", "2.jpg", "3.jpg"):
    (trip / name).write_bytes(name.encode() * 500)
expected = {f"viagem/{n}": n.encode() * 500 for n in ("1.jpg", "2.jpg", "3.jpg")}
expected.update({n: n.encode() * 1000 for n in ("a.jpg", "b.jpg", "c.jpg")})

real_rmtree = shutil.rmtree
def removal_dies_halfway(path, *args, **kwargs):
    # The copy of viagem/ is complete; deleting the original removes one
    # file and then fails, as a dying disk or a permission problem would.
    if Path(path).name == "viagem" and "media" in str(path) and "users" not in str(path):
        next(iter(sorted(Path(path).iterdir()))).unlink()
        raise OSError(5, "Input/output error")
    return real_rmtree(path, *args, **kwargs)
shutil.rmtree = removal_dies_halfway

import server
with TestClient(server.app) as client:
    code = (data / "setup_code").read_text().strip()
    failed = client.post("/api/setup", json={"code": code, "username": "admin", "password": "senha muito segura"})
    # The complete copy cannot go back over a half-removed original: the
    # account stays with it, setup closes, and the response says where it is.
    assert failed.status_code == 500, failed.text
    assert "viagem" in failed.json()["detail"]
    assert client.get("/healthz").json()["status"] == "ok"

def files_under(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} if root.exists() else {}

found = files_under(media)
for user_dir in (data / "users").glob("*/media") if (data / "users").exists() else []:
    for name, content in files_under(user_dir).items():
        found.setdefault(name, content)
missing = sorted(set(expected) - set(found))
assert not missing, f"lost files: {missing}"
assert all(found[name] == content for name, content in expected.items())
print("ok")
''')
    assert out.strip().endswith("ok")



_PROJECT_WITH_LIBRARY = r"""
import errno, os, sqlite3
from pathlib import Path
from fastapi.testclient import TestClient
from core.indexer_db import init_db

# A project as Meme Compass left it: copy-to-library under data/library/default
# with the absolute root of the folder it was imported from, in-place media in
# media/ (including a file named like a library file), and an item lost long ago.
data = Path("data")
data.mkdir()
conn = init_db(data / "iris_v1.db")
STALE_ROOT = "/home/someone/Projetos/Meme_Compass/data/library/default"
conn.execute(
    "INSERT INTO media_libraries (id, name, root_path, created_at) VALUES (1, 'default', ?, '')",
    (STALE_ROOT,),
)
library = data / "library" / "default"
(library / "2024").mkdir(parents=True)
(library / "2024" / "clip.mp4").write_bytes(b"video")
(library / "foto.jpg").write_bytes(b"library photo")
media = Path("media")
media.mkdir()
(media / "foto.jpg").write_bytes(b"media photo")
for caminho, relative, storage, library_id in (
    ("/old/clip.mp4", None, "2024/clip.mp4", 1),
    ("/old/foto.jpg", None, "foto.jpg", 1),
    (str(media.resolve() / "foto.jpg"), "foto.jpg", None, None),
    ("/old/lost.jpg", None, "lost.jpg", 1),
):
    conn.execute(
        "INSERT INTO memes (arquivo, caminho, relative_path, storage_path, library_id) VALUES (?, ?, ?, ?, ?)",
        (Path(caminho).name, caminho, relative, storage, library_id),
    )
conn.commit()
conn.close()
# A commit still only in the write-ahead log must travel with the catalog.
wal = sqlite3.connect(data / "iris_v1.db")
wal.execute("PRAGMA wal_autocheckpoint=0")
wal.execute("UPDATE memes SET tags = 'only in the WAL' WHERE storage_path = 'foto.jpg'")
wal.commit()
(data / "iris_v1.desc_embedding.vec").write_bytes(b"vectors")

def opened_files(db, media_root):
    # What the gallery would serve for each item, through the real engine.
    from core.search_engine import IrisEngine
    engine = IrisEngine(db_path=db, media_root=media_root, load_model=False)
    rows = sqlite3.connect(db).execute(
        "SELECT caminho, relative_path, storage_path, library_id FROM memes ORDER BY id"
    ).fetchall()
    out = []
    for caminho, relative, storage, library_id in rows:
        path = Path(engine.resolve_media_path(caminho, relative, storage_path=storage, library_id=library_id))
        out.append(path.read_bytes() if path.is_file() else None)
    return out

EXPECTED = [b"video", b"library photo", b"media photo", None]

def roots(db):
    with sqlite3.connect(db) as c:
        return [r[0] for r in c.execute("SELECT root_path FROM media_libraries ORDER BY id")]
"""


def test_setup_brings_the_library_folders_and_every_item_still_opens(tmp_path: Path) -> None:
    out = _run(tmp_path, _PROJECT_WITH_LIBRARY + r"""
import server
with TestClient(server.app) as client:
    summary = client.get("/api/setup").json()["legacy_summary"]
    assert summary == {"items": 4, "with_file": 3, "missing": 1, "outside": 0}, summary
    code = (data / "setup_code").read_text().strip()
    done = client.post("/api/setup", json={"code": code, "username": "admin", "password": "senha muito segura"})
    assert done.status_code == 201, done.text
wal.close()

user_root = (data / "users" / str(done.json()["user"]["id"])).resolve()
db, user_media = user_root / "iris.db", user_root / "media"
assert not library.exists() and not (data / "iris_v1.db").exists()
assert (user_root / "iris.desc_embedding.vec").read_bytes() == b"vectors"
assert roots(db) == [str(user_media / "library" / "default")]
with sqlite3.connect(db) as c:
    assert c.execute("SELECT tags FROM memes WHERE storage_path = 'foto.jpg'").fetchone()[0] == "only in the WAL"
assert opened_files(db, user_media) == EXPECTED
print("ok")
""")
    assert out.strip().endswith("ok")


def test_a_library_folder_that_fails_to_move_puts_everything_back(tmp_path: Path) -> None:
    out = _run(tmp_path, _PROJECT_WITH_LIBRARY + r"""
wal.close()
from core import first_setup
real_move = first_setup._move
def library_disk_fails(source, destination, *args, **kwargs):
    if Path(source).name == "default":
        raise OSError(errno.EIO, "Input/output error")
    return real_move(source, destination, *args, **kwargs)
first_setup._move = library_disk_fails

import server
with TestClient(server.app) as client:
    code = (data / "setup_code").read_text().strip()
    body = {"code": code, "username": "admin", "password": "senha muito segura"}
    failed = client.post("/api/setup", json=body)
    assert failed.status_code >= 500 and "Nada foi alterado" in failed.json()["detail"], failed.text
    assert client.get("/api/setup").json()["required"] is True

# The catalog is back with its original roots, next to its own files.
assert roots(data / "iris_v1.db") == [STALE_ROOT]
assert (data / "iris_v1.desc_embedding.vec").read_bytes() == b"vectors"
assert sorted(str(p.relative_to(library)) for p in library.rglob("*") if p.is_file()) == [
    "2024/clip.mp4", "foto.jpg",
]
assert (media / "foto.jpg").read_bytes() == b"media photo"
print("ok")
""")
    assert out.strip().endswith("ok")


def test_a_library_folder_never_lands_on_a_media_folder_of_the_same_name(tmp_path: Path) -> None:
    out = _run(tmp_path, _PROJECT_WITH_LIBRARY + r"""
wal.close()
(media / "library" / "default").mkdir(parents=True)
(media / "library" / "default" / "mine.jpg").write_bytes(b"mine")

import server
with TestClient(server.app) as client:
    code = (data / "setup_code").read_text().strip()
    done = client.post("/api/setup", json={"code": code, "username": "admin", "password": "senha muito segura"})
    assert done.status_code == 201, done.text

user_media = (data / "users" / str(done.json()["user"]["id"]) / "media").resolve()
assert (user_media / "library" / "default" / "mine.jpg").read_bytes() == b"mine"
assert sorted(p.name for p in (user_media / "library").iterdir()) == ["default"]
assert roots(user_media.parent / "iris.db") == [str(user_media / "legacy-libraries" / "default")]
assert opened_files(user_media.parent / "iris.db", user_media) == EXPECTED
print("ok")
""")
    assert out.strip().endswith("ok")
