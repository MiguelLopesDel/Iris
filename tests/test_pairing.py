"""Pairing codes: what they carry, who can see them, and the CA they may point to."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from core import pairing

ROOT = Path(__file__).resolve().parents[1]

# A throwaway self-signed CA, only ever used by these tests.
TEST_CA = """-----BEGIN CERTIFICATE-----
MIIBlTCCATugAwIBAgIUPlsOdOovoTHNjK3+5rV7w7HWXaMwCgYIKoZIzj0EAwIw
HzEdMBsGA1UEAwwUSXJpcyBQYWlyaW5nIFRlc3QgQ0EwIBcNMjYxMDAyMDExMzE1
WhgPMjEyNjA5MDgwMTEzMTVaMB8xHTAbBgNVBAMMFElyaXMgUGFpcmluZyBUZXN0
IENBMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE3vBH/cH1dCWHoMUXEDJCNEbU
RiPm3mIez3g/AXF+8jphB3S13U47IW/NFkgCOZ2bT5/rDtoDxc3Eud7dcvLFkqNT
MFEwHQYDVR0OBBYEFLK014gjiL34urA4dbAK5/HFlD7LMB8GA1UdIwQYMBaAFLK0
14gjiL34urA4dbAK5/HFlD7LMA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwID
SAAwRQIhAPgeApzuFZBdp0BJQ8htqqWYUo/5b8hjJEWeK3WSrjpmAiB7+I5D3BML
P+8cViTKTd5yWlM9POELpeVLlGixyq7s2w==
-----END CERTIFICATE-----
"""
TEST_CA_SHA256 = "130bf2fb86a1184b5e6f85618d4289c7d38ba1fd3a27c51651c2fb8555474627"


def test_addresses_are_normalised_to_scheme_host_and_port() -> None:
    assert pairing.normalize_address("https://Iris.Example/") == "https://iris.example"
    assert pairing.normalize_address("100.64.1.2:8501") == "http://100.64.1.2:8501"
    assert pairing.normalize_address("http://nas.lan:80") == "http://nas.lan"
    assert pairing.normalize_address("https://[fd00::1]:8443") == "https://[fd00::1]:8443"
    for bad in ("ftp://x", "https://iris.example/app", "https://user@iris.example",
                "https://user:secret@iris.example", "http://:80", "https://x:99999", "https://iris.example#x"):
        with pytest.raises(pairing.PairingError):
            pairing.normalize_address(bad)


def test_the_browser_address_comes_first_and_loopback_is_left_out() -> None:
    configured = pairing.parse_addresses("https://iris.example, http://100.64.1.2:8501\nhttps://iris.example")
    assert configured == ["https://iris.example", "http://100.64.1.2:8501"]
    assert pairing.pairing_addresses(configured, "http://192.168.1.20:8501") == [
        "http://192.168.1.20:8501", "https://iris.example", "http://100.64.1.2:8501",
    ]
    # A browser on the server itself: a phone cannot use 127.0.0.1.
    assert pairing.pairing_addresses(configured, "http://127.0.0.1:8501") == configured
    assert pairing.pairing_addresses(["http://localhost:8501"], None) == []
    with pytest.raises(pairing.PairingError):
        pairing.parse_addresses(" ".join(f"http://10.0.0.{i}" for i in range(9)))


def test_the_code_is_a_link_with_every_field() -> None:
    uri = pairing.pairing_uri("ab" * 16, ["https://iris.example", "http://10.0.0.2:8501"], TEST_CA_SHA256)
    parts = urlsplit(uri)
    assert (parts.scheme, parts.netloc) == ("iris", "pair")
    assert parse_qs(parts.query) == {
        "v": ["1"], "id": ["ab" * 16],
        "u": ["https://iris.example", "http://10.0.0.2:8501"], "ca": [TEST_CA_SHA256],
    }
    svg = pairing.qr_svg(uri)
    assert svg.startswith("<svg") and "</svg>" in svg
    # Scaled by CSS: without a viewBox (and with a fixed size) the browser crops it unreadable.
    head = svg[:svg.index(">")]
    assert "viewBox=" in head and "width=" not in head


def test_the_ca_is_validated_and_fingerprinted_like_openssl(tmp_path: Path) -> None:
    assert pairing.save_ca(tmp_path, "notes before\n" + TEST_CA + "notes after") == TEST_CA_SHA256
    assert pairing.ca_fingerprint(tmp_path) == TEST_CA_SHA256
    assert "BEGIN CERTIFICATE" in pairing.load_ca(tmp_path)
    for bad in ("", "-----BEGIN CERTIFICATE-----\nnot base64!\n-----END CERTIFICATE-----",
                TEST_CA + TEST_CA, "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"):
        with pytest.raises(pairing.PairingError):
            pairing.certificate_der(bad)
    pairing.remove_ca(tmp_path)
    assert pairing.ca_fingerprint(tmp_path) is None


def test_the_instance_id_is_stable(tmp_path: Path) -> None:
    first = pairing.instance_id(tmp_path)
    assert len(first) == 32 and pairing.instance_id(tmp_path) == first


_SERVER = r'''
import json
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.users_db import create_user

data = Path("data")
for name, admin in (("root", True), ("ana", False)):
    create_user(data / "users.db", data, username=name, is_admin=admin,
                password_hash=hash_password("synthetic password 1"))
import server

def signed_in(name):
    client = TestClient(server.app)
    client.__enter__()
    assert client.post("/api/auth/login", data={"username": name, "password": "synthetic password 1"}).status_code == 200
    return client

root, ana = signed_in("root"), signed_in("ana")
anonymous = TestClient(server.app)
out = {}
out["health_id"] = anonymous.get("/healthz").json()["instance_id"]
out["anonymous_code"] = anonymous.get("/api/pairing").status_code
out["no_address"] = ana.get("/api/pairing", params={"current": "http://127.0.0.1:8501"}).json()
out["bad_setting"] = root.put("/api/admin/settings", json={"pairing_addresses": "ftp://x"}).status_code
out["setting"] = root.put("/api/admin/settings", json={"pairing_addresses": "https://iris.example 100.64.1.2:8501"}).status_code
out["ana_ca"] = ana.put("/api/admin/pairing/ca", json={"pem": CA}).status_code
out["bad_ca"] = root.put("/api/admin/pairing/ca", json={"pem": "nope"}).status_code
out["root_ca"] = root.put("/api/admin/pairing/ca", json={"pem": CA}).json()
out["code"] = ana.get("/api/pairing", params={"current": "http://192.168.1.20:8501"}).json()
out["public_ca"] = anonymous.get("/api/pairing/ca.pem").text
out["removed"] = root.delete("/api/admin/pairing/ca").json()
out["ca_after"] = anonymous.get("/api/pairing/ca.pem").status_code
print(json.dumps(out))
'''


def test_signed_in_people_get_a_code_and_only_administrators_set_its_ca(tmp_path: Path) -> None:
    import json

    env = dict(os.environ, PYTHONPATH=str(ROOT), IRIS_SERVER_MODE="private", IRIS_LOAD_MODEL="0",
               IRIS_SESSION_HTTPS_ONLY="false")
    script = f"CA = {TEST_CA!r}\n" + _SERVER
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])

    assert len(out["health_id"]) == 32
    assert out["anonymous_code"] == 401
    assert out["no_address"]["addresses"] == [] and out["no_address"]["qr_svg"] is None
    assert out["bad_setting"] == 422 and out["setting"] == 200
    assert out["ana_ca"] == 403 and out["bad_ca"] == 422
    assert out["root_ca"] == {"ca_sha256": TEST_CA_SHA256}

    code = out["code"]
    assert code["instance_id"] == out["health_id"]
    assert code["addresses"] == ["http://192.168.1.20:8501", "https://iris.example", "http://100.64.1.2:8501"]
    assert code["ca_sha256"] == TEST_CA_SHA256
    assert parse_qs(urlsplit(code["uri"]).query)["ca"] == [TEST_CA_SHA256]
    assert code["qr_svg"].startswith("<svg")
    assert "BEGIN CERTIFICATE" in out["public_ca"]
    assert out["removed"] == {"ca_sha256": None} and out["ca_after"] == 404
