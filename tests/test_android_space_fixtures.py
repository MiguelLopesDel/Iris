"""Fixtures of the shared-space routes, for the Android client's decode tests.

The sibling ``test_android_contract_fixtures.py`` runs the server without
accounts; spaces only exist between accounts, so these are produced by the
real server in private mode, in a subprocess. Same contract: the Android tests
decode these files, and this test fails when the server's shape drifts.

    IRIS_UPDATE_FIXTURES=1 pytest tests/test_android_space_fixtures.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "android/app/src/test/resources/fixtures"

# Timestamps, content hashes and upload ids change on every run; sizes and
# hashes of a generated JPEG change with the Pillow build. Masked, but a
# number stays a number: the Kotlin models decode these files.
_MASKED_TEXT = {"added_at", "created_at", "removed_at", "purge_after", "sha256", "upload_id"}
_MASKED_NUMBERS = {"size_bytes"}

_SCRIPT = r'''
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user

PASSWORD = "synthetic fixture password"
data = Path("data")
for name, admin in (("ana", True), ("bruno", False)):
    user = create_user(data / "users.db", data, username=name, display_name=name.title(),
                       password_hash=hash_password(PASSWORD), is_admin=admin)
    init_db(user.db_path).close()
    image = user.media_root / f"{name}.jpg"
    Image.new("RGB", (32, 24), (200, 80, 40)).save(image)
    with sqlite3.connect(user.db_path) as conn:
        conn.execute("INSERT INTO memes (arquivo, caminho, embedding, descricao_ia) VALUES (?, ?, ?, ?)",
                     (image.name, str(image), b"\0" * 16, "praia ao pôr do sol"))

import server

out = {}
with TestClient(server.app) as ana, TestClient(server.app) as bruno:
    for name, client in (("ana", ana), ("bruno", bruno)):
        client.post("/api/auth/login", data={"username": name, "password": PASSWORD})
    space = ana.post("/api/spaces", json={"name": "Família"}).json()["space"]["id"]
    ana.post(f"/api/spaces/{space}/members", json={"username": "bruno", "role": "viewer"})
    out["space_created.json"] = ana.post("/api/spaces", json={"name": "Viagem"}).json()
    out["space_item_added.json"] = ana.post(f"/api/spaces/{space}/items", json={"record_id": 1}).json()
    # A viewer's view: no remove action, and the author's name is present.
    out["spaces.json"] = bruno.get("/api/spaces").json()
    out["space_detail.json"] = bruno.get(f"/api/spaces/{space}").json()
    out["space_items.json"] = bruno.get(f"/api/spaces/{space}/items?limit=60").json()
    out["space_members.json"] = bruno.get(f"/api/spaces/{space}/members").json()
    out["space_item_saved.json"] = bruno.post(f"/api/spaces/{space}/items/1/save").json()
    album = ana.post(f"/api/spaces/{space}/albums", json={"name": "Praia"}).json()["album"]["id"]
    ana.post(f"/api/spaces/{space}/albums/{album}/items", json={"item_ids": [1]})
    out["space_albums.json"] = bruno.get(f"/api/spaces/{space}/albums").json()
    out["space_album_items.json"] = bruno.get(f"/api/spaces/{space}/albums/{album}/items").json()
    out["space_search.json"] = bruno.get(f"/api/spaces/{space}/search?q=praia").json()
print(json.dumps(out))
'''


def _mask(value):
    if isinstance(value, dict):
        return {
            key: (
                "<masked>" if key in _MASKED_TEXT and value[key] is not None
                else 0 if key in _MASKED_NUMBERS
                else _mask(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask(item) for item in value]
    return value


def test_android_space_fixtures_are_current(tmp_path: Path) -> None:
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        IRIS_LOAD_MODEL="0",
        IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false",
    )
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    responses = json.loads(result.stdout.strip().splitlines()[-1])

    updating = os.environ.get("IRIS_UPDATE_FIXTURES") == "1"
    stale: list[str] = []
    for filename, payload in sorted(responses.items()):
        served = json.dumps(_mask(payload), indent=2, ensure_ascii=False)
        target = FIXTURE_DIR / filename
        if updating or not target.exists():
            target.write_text(served + "\n", encoding="utf-8")
            continue
        if target.read_text(encoding="utf-8").strip() != served.strip():
            stale.append(filename)
    assert not stale, (
        f"O formato das rotas de espaços mudou: {', '.join(stale)}.\n"
        "  IRIS_UPDATE_FIXTURES=1 pytest tests/test_android_space_fixtures.py"
    )


def test_space_fixtures_exercise_a_viewer() -> None:
    """The viewer case is the one the app must not offer a remove action for."""
    items = json.loads((FIXTURE_DIR / "space_items.json").read_text(encoding="utf-8"))
    assert items["items"] and all(item["can_remove"] is False for item in items["items"])
    spaces = json.loads((FIXTURE_DIR / "spaces.json").read_text(encoding="utf-8"))
    assert [space["role"] for space in spaces["spaces"]] == ["viewer"]
