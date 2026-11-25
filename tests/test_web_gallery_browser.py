"""The web gallery, in a real browser against a real server.

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
ITEMS = 30  # fewer than one batch
BATCH = 120  # gallery.js BATCH_SIZE


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path, request):
    # Indirect parameters: {"items": n, "spacing": seconds between photos}.
    options = getattr(request, "param", {})
    items = options.get("items", ITEMS)
    spacing = options.get("spacing", 1)
    env = dict(
        os.environ, PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false", IRIS_LOAD_MODEL="0", IRIS_THUMBNAIL_WARMUP="0", IRIS_BACKEND_PRELOAD="0",
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
            f"    for i in range({items}):\n"
            "        name = f'IMG_{i:04}.jpg'\n"
            "        Image.new('RGB', (8, 8), (i * 8 % 256, 80, 160)).save(ana.media_root / name)\n"
            "        conn.execute('INSERT INTO memes (arquivo, caminho, file_mtime, embedding)'\n"
            "                     ' VALUES (?, ?, ?, NULL)',"
            f" (name, str(ana.media_root / name), 1.7e9 + i * {spacing}))\n"
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
        sync_api.expect(tiles).to_have_count(ITEMS)
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
        sync_api.expect(tiles).to_have_count(ITEMS)
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


@pytest.mark.parametrize("server", [{"items": BATCH + 10, "spacing": 86_400}], indirect=True)
def test_the_gallery_scrolls_through_the_library_with_days_side_by_side(server) -> None:
    base, _ = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        tiles = page.locator("#gallery-grid .media-card")
        sync_api.expect(page.locator("#gallery-page-info")).to_have_text(f"{BATCH + 10} itens")
        sync_api.expect(tiles).to_have_count(BATCH)

        # One photo a day: days share a line instead of each taking a row.
        groups = page.locator("#gallery-grid .day-group")
        first, second = groups.nth(0).bounding_box(), groups.nth(1).bounding_box()
        assert abs(first["y"] - second["y"]) < 2 and second["x"] > first["x"]

        # Scrolling to the end brings the next batch, without a pager.
        page.locator("#gallery-sentinel").scroll_into_view_if_needed()
        sync_api.expect(tiles).to_have_count(BATCH + 10)
        sync_api.expect(page.locator("#gallery-sentinel")).to_contain_text("começo da biblioteca")
        # The viewer walks everything loaded, not one page of it.
        tiles.last.locator("img").click()
        sync_api.expect(page.locator("#detail-aside .detail-meta")).to_contain_text("IMG_0000.jpg")
        page.keyboard.press("Escape")

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


def test_an_open_gallery_follows_library_changes_without_a_reload(server) -> None:
    """Media a phone syncs (or another tab removes) shows up in the open page."""
    base, _media = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        first = page.locator("#gallery-grid .media-card").first
        sync_api.expect(first).to_have_attribute("title", "IMG_0029.jpg")
        page.evaluate("window.__irisNoReload = true")

        # Outside the page, with the same session: as another tab would.
        response = page.request.post(base + "/api/trash", multipart={"db_ids": "30"})
        assert response.status == 200

        sync_api.expect(first).to_have_attribute("title", "IMG_0028.jpg", timeout=20_000)
        # Same document: the change arrived without navigating.
        assert page.evaluate("window.__irisNoReload") is True
        browser.close()


def test_a_heart_in_the_viewer_puts_the_photo_under_favourites(server) -> None:
    base, _media = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        tiles = page.locator("#gallery-grid .media-card")
        sync_api.expect(tiles).to_have_count(ITEMS)
        tiles.first.locator("img").click()

        heart = page.locator("#detail-aside [data-detail-action=favorite]")
        sync_api.expect(heart).to_have_attribute("aria-pressed", "false")
        heart.click()
        sync_api.expect(heart).to_have_attribute("aria-pressed", "true")
        page.keyboard.press("Escape")

        page.select_option("#filtro-media-type", "favorites")
        sync_api.expect(tiles).to_have_count(1)
        sync_api.expect(tiles.first).to_have_attribute("title", "IMG_0029.jpg")
        browser.close()


def test_the_home_page_shows_this_day_in_earlier_years(server) -> None:
    """The seeded photos were taken on 14 November 2023 (UTC)."""
    import datetime as dt

    base, _media = server
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # browser binaries missing
            if REQUIRED:
                raise
            pytest.skip(f"Chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        taken = dt.datetime.fromtimestamp(1.7e9 + 29)
        page.clock.set_fixed_time(taken.replace(year=taken.year + 2, hour=12))
        page.goto(base + "/login")
        page.fill("input[name=username]", "ana")
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(base + "/")
        page.evaluate("window.location.hash = 'home'")

        section = page.locator("#home-memories-section")
        sync_api.expect(section).to_be_visible()
        sync_api.expect(section.locator(".home-memory-year")).to_have_text(f"Há 2 anos · {taken.year}")
        section.locator(".home-recent-card").first.click()
        sync_api.expect(page.locator("#detail-aside .detail-meta")).to_contain_text("IMG_00")
        browser.close()


def test_albums_show_their_cover_and_open_as_a_grid_of_photos(server) -> None:
    base, _media = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        created = page.request.post(base + "/api/collections", multipart={"name": "Praia"})
        album = created.json()["collection_id"]
        page.request.post(f"{base}/api/collections/{album}/members", multipart={"db_ids": "3,4,5"})

        page.evaluate("window.location.hash = 'collections'")
        card = page.locator(".album-card", has_text="Praia")
        sync_api.expect(card.locator(".album-cover img")).to_be_visible()
        sync_api.expect(card.locator(".album-count")).to_have_text("3 itens")

        card.click()
        sync_api.expect(page.locator(".album-head h2")).to_have_text("Praia")
        photos = page.locator(".album-photos .media-card")
        sync_api.expect(photos).to_have_count(3)
        photos.first.locator("img").click()
        sync_api.expect(page.locator("#detail-aside .detail-meta")).to_contain_text("IMG_")
        page.keyboard.press("Escape")

        page.get_by_role("button", name="← Álbuns").click()
        sync_api.expect(card).to_be_visible()
        browser.close()


def test_shift_click_selects_a_range_and_escape_lets_go(server) -> None:
    base, _media = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        tiles = page.locator("#gallery-grid .media-card")
        sync_api.expect(tiles).to_have_count(ITEMS)

        tiles.nth(0).locator(".media-checkbox").check()
        # While choosing, a click on a photo selects instead of opening it.
        tiles.nth(4).locator("img").click(modifiers=["Shift"])
        sync_api.expect(page.locator("#floating-count")).to_have_text("5 selecionado(s)")
        sync_api.expect(page.locator("#detail-modal")).to_be_hidden()
        tiles.nth(9).locator("img").click()
        sync_api.expect(page.locator("#floating-count")).to_have_text("6 selecionado(s)")

        page.keyboard.press("Escape")
        sync_api.expect(page.locator("#gallery-grid .media-checkbox:checked")).to_have_count(0)
        # With nothing chosen, a click opens the photo again.
        tiles.nth(1).locator("img").click()
        sync_api.expect(page.locator("#detail-modal")).to_be_visible()
        browser.close()


def test_viewer_shortcuts_favourite_and_fold_the_information(server) -> None:
    base, _media = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        page.locator("#gallery-grid .media-card img").first.click()
        aside = page.locator("#detail-aside")
        heart = aside.locator("[data-detail-action=favorite]")
        sync_api.expect(heart).to_have_attribute("aria-pressed", "false")

        page.keyboard.press("f")
        sync_api.expect(heart).to_have_attribute("aria-pressed", "true")
        page.keyboard.press("i")
        sync_api.expect(aside).to_be_hidden()
        page.keyboard.press("i")
        sync_api.expect(aside).to_be_visible()
        browser.close()


def test_files_dropped_on_the_page_are_sent_to_the_library(server) -> None:
    base, media = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        sync_api.expect(page.locator("#gallery-grid .media-card")).to_have_count(ITEMS)
        page.evaluate("""() => {
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = 4;
            return new Promise((resolve) => canvas.toBlob((blob) => {
                const transfer = new DataTransfer();
                transfer.items.add(new File([blob], 'dropped.png', { type: 'image/png' }));
                document.body.dispatchEvent(new DragEvent('drop', {
                    dataTransfer: transfer, bubbles: true, cancelable: true,
                }));
                resolve();
            }, 'image/png'));
        }""")
        sync_api.expect(page.locator("#import-status")).to_contain_text("Enviados 1", timeout=30_000)
        browser.close()


def test_settings_let_the_admin_choose_where_ai_runs_and_anyone_opt_out(server) -> None:
    base, _media = server
    with sync_api.sync_playwright() as playwright:
        browser, page = _signed_in_gallery(playwright, base)
        page.evaluate("window.location.hash = 'system'")
        nav = page.locator(".settings-nav")
        sync_api.expect(nav.get_by_role("link", name="Processamento por IA")).to_be_visible()

        # Administrator: the cards set the mode; saving keeps it.
        nav.get_by_role("link", name="Processamento por IA").click()
        panel = page.locator("#ai-settings")
        panel.locator('[data-ai-mode="off"]').click()
        sync_api.expect(panel.locator('[data-ai-mode="off"]')).to_have_attribute("aria-pressed", "true")
        panel.get_by_role("button", name="Salvar").click()
        sync_api.expect(panel.locator("[data-settings-status]")).to_contain_text("salvas")
        assert page.request.get(base + "/api/admin/ai").json()["mode"] == "off"

        # Anyone: their own photos' analysis, on by default.
        mine = page.locator("#my-ai")
        sync_api.expect(mine.locator("#my-ai-enabled")).to_be_checked()
        mine.get_by_text("Analisar minhas fotos com IA").click()
        sync_api.expect(mine.locator("#my-ai-status")).to_contain_text("desligada")
        assert page.request.get(base + "/api/me/ai").json()["enabled"] is False
        browser.close()
