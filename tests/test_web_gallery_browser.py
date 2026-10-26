"""The web gallery's pager, in a real browser against a real server.

Skipped where Playwright and its Chromium are not installed, except when
IRIS_REQUIRE_BROWSER_TESTS=1 (as in CI), where a missing browser is a failure.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REQUIRED = os.environ.get("IRIS_REQUIRE_BROWSER_TESTS") == "1"
if REQUIRED:
    from playwright import sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "synthetic password 1"
ITEMS = 30  # three pages of 12


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path):
    env = dict(
        os.environ, PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false", IRIS_LOAD_MODEL="0", IRIS_THUMBNAIL_WARMUP="0",
    )
    subprocess.run(
        [sys.executable, "-c", (
            "import sqlite3\n"
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
            f"    for i in range({ITEMS}):\n"
            "        name = f'IMG_{i:04}.jpg'\n"
            "        Image.new('RGB', (8, 8), (i * 8, 80, 160)).save(ana.media_root / name)\n"
            "        conn.execute('INSERT INTO memes (arquivo, caminho, file_mtime, embedding)'\n"
            "                     ' VALUES (?, ?, ?, NULL)', (name, str(ana.media_root / name), 1.7e9 + i))\n"
        )],
        cwd=tmp_path, env=env, check=True, timeout=60,
    )
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--app-dir", str(ROOT),
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
        yield base
    finally:
        process.terminate()
        process.wait(timeout=20)


def test_the_pager_under_the_grid_moves_between_pages(server) -> None:
    base = server
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # browser binaries missing
            if REQUIRED:
                raise
            pytest.skip(f"Chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base + "/login")
        page.fill("input[name=username]", "ana")
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(base + "/")
        page.evaluate("window.location.hash = 'gallery'")
        page.get_by_text("Organizar visualização").click()
        page.select_option("#gallery-per-page", "12")

        pager = page.locator("#gallery-pagination-bottom")
        sync_api.expect(pager).to_contain_text("Página 1 de 3")
        tiles = page.locator("#gallery-grid .media-card")
        sync_api.expect(tiles).to_have_count(12)
        # Sorted by date by default, under a heading per day, as in the app.
        sync_api.expect(page.locator("#gallery-grid .grid-day").first).to_be_visible()

        pager.get_by_role("button", name="Próxima →").click()
        sync_api.expect(pager).to_contain_text("Página 2 de 3")
        sync_api.expect(pager.locator("[aria-current=page]")).to_have_text("2")

        pager.get_by_role("button", name="Página 3").click()
        sync_api.expect(pager).to_contain_text("Página 3 de 3")
        sync_api.expect(pager.get_by_role("button", name="Próxima →")).to_be_disabled()
        sync_api.expect(tiles).to_have_count(ITEMS - 24)

        # Adding photos is one click from the gallery, not hidden in Organizar.
        page.get_by_role("button", name="+ Adicionar fotos").click()
        sync_api.expect(page.locator("#import-section")).to_be_in_viewport()
        browser.close()
