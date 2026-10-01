"""The browser upload, end to end in a real browser against a real server.

Skipped where Playwright and its Chromium are not installed, except when
IRIS_REQUIRE_BROWSER_TESTS=1 (as in CI), where a missing browser is a failure.
"""
from __future__ import annotations

import hashlib
import os
import socket
import sqlite3
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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path):
    env = dict(
        os.environ, PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private",
        IRIS_SESSION_HTTPS_ONLY="false", IRIS_LOAD_MODEL="0",
    )
    subprocess.run(
        [sys.executable, "-c", (
            "from pathlib import Path\n"
            "from core.auth import hash_password\n"
            "from core.users_db import create_user\n"
            f"create_user(Path('data/users.db'), Path('data'), username='ana', is_admin=True,"
            f" password_hash=hash_password({PASSWORD!r}))\n"
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
        yield base, tmp_path / "data" / "users" / "1"
    finally:
        process.terminate()
        process.wait(timeout=20)


def _catalog(account: Path) -> dict[str, str]:
    with sqlite3.connect(account / "iris.db") as conn:
        return {row[0]: row[1] for row in conn.execute("SELECT content_hash, arquivo FROM memes")}


def test_a_folder_goes_up_resumes_after_a_failure_and_skips_what_is_known(server, tmp_path: Path) -> None:
    base, account = server
    folder = tmp_path / "Viagem"
    (folder / "dia 2").mkdir(parents=True)
    small = {}
    for i in range(20):  # more than one reservation batch
        path = folder / ("dia 2" if i % 2 else "") / f"foto-{i:02}.jpg"
        path.write_bytes(b"\xff\xd8\xff" + os.urandom(2000) + bytes([i]))
        small[path.name] = path
    video = folder / "video.mp4"
    video.write_bytes(os.urandom(40 * 1024 * 1024))  # two chunks

    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:  # browser binaries missing
            if REQUIRED:
                raise
            pytest.skip(f"Chromium unavailable: {exc}")
        page = browser.new_page()
        page.goto(base + "/login")
        page.fill("input[name=username]", "ana")
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(base + "/")
        page.evaluate("window.location.hash = 'system'")
        status = page.locator("#import-status")

        # First run: the server refuses the video's second chunk.
        def refuse_second_chunk(route):
            if route.request.method == "PUT" and "offset=0" not in route.request.url:
                route.fulfill(status=422, body='{"detail":"refused by the test"}')
            else:
                route.continue_()
        page.route("**/api/sync/uploads/*", refuse_second_chunk)
        page.locator("#import-files").set_input_files([str(small["foto-00.jpg"])])
        page.click("#import-start")
        sync_api.expect(status).to_contain_text("Concluído", timeout=60_000)
        page.locator("#import-files").set_input_files([])
        page.locator("#import-folder-files").set_input_files(str(folder))
        page.click("#import-start")
        sync_api.expect(status).to_contain_text("Concluído", timeout=120_000)
        sync_api.expect(status).to_contain_text("1 já estavam na biblioteca")
        sync_api.expect(status).to_contain_text("1 com falha")
        sync_api.expect(page.locator("#import-failures")).to_contain_text("refused by the test")
        assert len(_catalog(account)) == 20
        partial = list((account / "sync_uploads").glob("*.part"))
        assert [p.stat().st_size for p in partial] == [32 * 1024 * 1024]

        # Second run, same folder: only the video's remaining bytes travel.
        page.unroute("**/api/sync/uploads/*")
        sent = []
        page.on("request", lambda request: sent.append(request) if request.method == "PUT" else None)
        page.click("#import-start")
        sync_api.expect(status).to_contain_text("Concluído", timeout=120_000)
        sync_api.expect(status).to_contain_text("32 MB retomados")
        sync_api.expect(status).to_contain_text("20 já estavam na biblioteca")
        assert len(sent) == 1 and "offset=33554432" in sent[0].url, [r.url for r in sent]

        # The browser is listed among the devices as this browser.
        page.reload()
        sync_api.expect(page.locator("#devices-list")).to_contain_text("este navegador")
        browser.close()

    catalog = _catalog(account)
    assert len(catalog) == 21
    assert catalog[hashlib.sha256(video.read_bytes()).hexdigest()] == "video.mp4"
