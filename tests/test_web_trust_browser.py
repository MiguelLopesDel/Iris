"""The Sistema card that lets a computer trust the server's own certificate, in a real browser.

Skipped where Playwright and its Chromium are not installed, except when
IRIS_REQUIRE_BROWSER_TESTS=1 (as in CI), where a missing browser is a failure.
"""
from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

REQUIRED = os.environ.get("IRIS_REQUIRE_BROWSER_TESTS") == "1"
if REQUIRED:
    from playwright import sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "synthetic password 1"
SCREENSHOT = os.environ.get("IRIS_TRUST_SCREENSHOT")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(tmp_path: Path, tls_mode: str):
    env = dict(
        os.environ, PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private", IRIS_TLS=tls_mode,
        IRIS_SESSION_HTTPS_ONLY="false", IRIS_LOAD_MODEL="0",
    )
    subprocess.run(
        [sys.executable, "-c", (
            "from pathlib import Path\n"
            "from core import tls\n"
            "from core.auth import hash_password\n"
            "from core.users_db import create_user\n"
            "for name, admin in (('ana', True), ('bob', False)):\n"
            "    create_user(Path('data/users.db'), Path('data'), username=name, is_admin=admin,"
            f" password_hash=hash_password({PASSWORD!r}))\n"
            # What scripts/serve.py does before listening when IRIS_TLS=self.
            "if tls.mode_from_env() == 'self':\n"
            "    tls.ensure_self_signed(Path('data'), tls.certificate_names([], 'iris.lan'))\n"
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
    for _ in range(200):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        process.terminate()
        pytest.fail("server did not start")
    return base, process


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except Exception as exc:  # browser binaries missing
            if REQUIRED:
                raise
            pytest.skip(f"Chromium unavailable: {exc}")
        yield launched
        launched.close()


def _open_system(browser, base: str, username: str):
    page = browser.new_page(viewport={"width": 1280, "height": 1400})
    page.goto(base + "/login")
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(base + "/")
    page.evaluate("window.location.hash = 'system'")
    return page


def test_anyone_can_download_the_authority_and_check_it_by_eye(browser, tmp_path: Path) -> None:
    base, process = _serve(tmp_path, "self")
    try:
        page = _open_system(browser, base, "bob")
        card = page.locator("#computer-trust")
        sync_api.expect(card).to_be_visible(timeout=15_000)
        sync_api.expect(card).to_contain_text("iris.lan")

        with page.expect_download() as download:
            page.click("#computer-trust-download")
        saved = download.value
        assert saved.suggested_filename.startswith("iris-") and saved.suggested_filename.endswith(".crt")
        pem = Path(saved.path()).read_bytes()
        assert pem.startswith(b"-----BEGIN CERTIFICATE-----")

        der = x509.load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)
        # The fingerprint the card shows is the one of the file it hands out.
        shown = card.locator(".pairing-fingerprint").inner_text().replace(":", "").lower()
        assert shown == hashlib.sha256(der).hexdigest()

        page.locator(".trust-steps summary", has_text="Firefox").click()
        sync_api.expect(card).to_contain_text("Confiar nesta CA para identificar sites")
        if SCREENSHOT:
            card.screenshot(path=SCREENSHOT)
    finally:
        process.terminate()
        process.wait(timeout=20)


def test_the_card_stays_hidden_without_a_certificate_of_its_own(browser, tmp_path: Path) -> None:
    base, process = _serve(tmp_path, "off")
    try:
        page = _open_system(browser, base, "ana")
        sync_api.expect(page.locator("#pairing-settings")).to_be_visible(timeout=15_000)
        sync_api.expect(page.locator("#computer-trust")).to_be_hidden()
        try:
            urllib.request.urlopen(base + "/api/tls/ca.crt")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            pytest.fail("a server without its own certificate handed out an authority")
    finally:
        process.terminate()
        process.wait(timeout=20)
