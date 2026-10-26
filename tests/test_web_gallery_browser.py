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
            "from core.users_db import create_device\n"
            "for name in ('Celular antigo', 'Tablet antigo'):\n"
            "    device = create_device(Path('data/users.db'), ana.id, name, 'android', name)\n"
            "    with sqlite3.connect('data/users.db') as conn:\n"
            "        conn.execute(\"UPDATE devices SET last_seen_at = '2020-01-01T00:00:00+00:00' WHERE id = ?\", (device.id,))\n"
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
        yield base, tmp_path / "data" / "users" / "1" / "media"
    finally:
        process.terminate()
        process.wait(timeout=20)


def _signed_in_gallery(playwright, base: str):
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
    return browser, page


def test_the_viewer_renames_and_moves_a_photo_to_the_trash(server) -> None:
    base, media = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        tiles = page.locator("#gallery-grid .media-card")
        sync_api.expect(tiles).to_have_count(24)
        tiles.first.locator("img").click()

        aside = page.locator("#detail-aside")
        # Headed by when the photo was taken, as in the app; the file name is secondary.
        sync_api.expect(aside.locator(".detail-title")).to_contain_text(" · ")
        sync_api.expect(aside.locator(".detail-meta")).to_contain_text("IMG_0029.jpg")
        sync_api.expect(aside.get_by_role("link", name="Baixar")).to_have_attribute("download", "IMG_0029.jpg")

        aside.get_by_role("button", name="Renomear").click()
        page.locator(".app-modal input").fill("praia")
        page.locator(".app-modal").get_by_role("button", name="Renomear").click()
        sync_api.expect(aside.locator(".detail-meta")).to_contain_text("praia.jpg")
        assert (media / "praia.jpg").is_file() and not (media / "IMG_0029.jpg").exists()

        aside.get_by_role("button", name="Mover para a lixeira").click()
        page.locator(".app-modal").get_by_role("button", name="Mover para a lixeira").click()
        # The viewer moves on to the next photo, and the gallery behind it lost one.
        sync_api.expect(aside.locator(".detail-meta")).to_contain_text("IMG_0028.jpg")
        page.keyboard.press("Escape")
        sync_api.expect(page.locator("#gallery-page-info")).to_contain_text(f"{ITEMS - 1} itens")
        browser.close()


def test_after_the_trash_the_viewer_still_shows_and_acts_on_each_photo(server) -> None:
    base, media = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        tiles = page.locator("#gallery-grid .media-card")
        sync_api.expect(tiles).to_have_count(24)
        tiles.nth(2).locator("img").click()
        aside = page.locator("#detail-aside")
        sync_api.expect(aside.locator(".detail-meta")).to_contain_text("IMG_0027.jpg")

        aside.get_by_role("button", name="Mover para a lixeira").click()
        page.locator(".app-modal").get_by_role("button", name="Mover para a lixeira").click()
        sync_api.expect(aside.locator(".detail-meta")).to_contain_text("IMG_0026.jpg")
        # Photos after the one that left moved up a place in the catalogue; the
        # viewer must still show IMG_0028 here, not its neighbour.
        page.keyboard.press("ArrowLeft")
        sync_api.expect(aside.locator(".detail-meta")).to_contain_text("IMG_0028.jpg")
        aside.get_by_role("button", name="Renomear").click()
        page.locator(".app-modal input").fill("praia")
        page.locator(".app-modal").get_by_role("button", name="Renomear").click()
        sync_api.expect(aside.locator(".detail-meta")).to_contain_text("praia.jpg")
        assert (media / "praia.jpg").is_file()
        assert (media / "IMG_0029.jpg").is_file() and not (media / "IMG_0028.jpg").exists()
        browser.close()


def test_the_pager_under_the_grid_moves_between_pages(server) -> None:
    base, _ = server
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


def test_old_devices_are_folded_and_can_all_be_disconnected(server) -> None:
    base, _ = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        page.evaluate("window.location.hash = 'system'")
        devices = page.locator("#devices-list")
        # This browser stays in view; the two unused for years are folded together.
        sync_api.expect(devices).to_contain_text("este navegador")
        stale = devices.locator(".device-stale")
        sync_api.expect(stale.locator("summary")).to_have_text("2 aparelhos sem uso há mais de 30 dias")
        sync_api.expect(stale.get_by_text("Celular antigo")).to_be_hidden()

        stale.locator("summary").click()
        stale.get_by_role("button", name="Desconectar todos esses").click()
        page.locator(".app-modal").get_by_role("button", name="Desconectar todos").click()
        sync_api.expect(stale).to_have_count(0)
        sync_api.expect(devices).to_contain_text("este navegador")
        browser.close()
