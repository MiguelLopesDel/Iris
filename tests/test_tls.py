"""HTTPS served by Iris itself: the self-signed certificate and the administrator's own."""

from __future__ import annotations

import datetime as dt
import ssl
import sys
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from core import server_identity, tls

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import serve  # noqa: E402


def _spki(certificate: x509.Certificate) -> bytes:
    return certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _issue(tmp_path: Path, days: int = 90, key=None) -> tuple[Path, Path]:
    """A certificate and key like an administrator would bring, signed by a throwaway CA."""
    key = key or ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "iris.example")])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=400)).not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("iris.example")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    certfile.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ))
    return certfile, keyfile


def test_the_mode_comes_from_the_environment() -> None:
    assert tls.mode_from_env({}) == "off"
    assert tls.mode_from_env({"IRIS_TLS": " Self "}) == "self"
    with pytest.raises(tls.TlsConfigError):
        tls.mode_from_env({"IRIS_TLS": "on"})


def test_the_certificate_names_the_pairing_hosts_extras_and_loopback() -> None:
    names = tls.certificate_names(
        ["https://Iris.Example", "http://100.64.1.2:8501", "https://iris.example:443"], "home.lan 192.168.1.20"
    )

    assert names == ["iris.example", "100.64.1.2", "home.lan", "192.168.1.20", "localhost", "127.0.0.1"]


def test_the_self_signed_certificate_carries_the_identity_key(tmp_path: Path) -> None:
    files = tls.ensure_self_signed(tmp_path, ["iris.example", "192.168.1.20"])

    certificate = x509.load_pem_x509_certificate(files.certfile.read_bytes())
    # The key a paired device pins is the key this certificate presents.
    assert _spki(certificate) == server_identity.server_identity(tmp_path).public_key_der
    assert files.keyfile == tmp_path / server_identity.KEY_FILE
    assert tls._names_of(certificate) == {"iris.example", "192.168.1.20"}
    # uvicorn's ssl module loads it with its key.
    ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(files.certfile, files.keyfile)


def test_the_certificate_is_reissued_only_when_names_change_or_it_nears_its_end(tmp_path: Path) -> None:
    first = tls.ensure_self_signed(tmp_path, ["iris.example"]).certfile.read_bytes()
    assert tls.ensure_self_signed(tmp_path, ["iris.example"]).certfile.read_bytes() == first

    renamed = tls.ensure_self_signed(tmp_path, ["iris.example", "home.lan"]).certfile.read_bytes()
    assert renamed != first

    late = dt.datetime.now(dt.UTC) + tls.SELF_VALIDITY - dt.timedelta(days=1)
    renewed = tls.ensure_self_signed(tmp_path, ["iris.example", "home.lan"], now=late).certfile.read_bytes()
    assert renewed != renamed
    # A reissue never changes the key: devices keep their pin.
    for pem in (first, renamed, renewed):
        assert _spki(x509.load_pem_x509_certificate(pem)) == server_identity.server_identity(tmp_path).public_key_der


def test_a_custom_certificate_is_checked_before_the_server_starts(tmp_path: Path) -> None:
    certfile, keyfile = _issue(tmp_path)
    env = {"IRIS_TLS_CERT": str(certfile), "IRIS_TLS_KEY": str(keyfile)}

    assert tls.custom_files(tmp_path, env) == tls.TlsFiles(certfile, keyfile)

    with pytest.raises(tls.TlsConfigError, match="not found"):
        tls.custom_files(tmp_path, {**env, "IRIS_TLS_KEY": str(tmp_path / "missing.pem")})
    (tmp_path / "other").mkdir()
    _, other_key = _issue(tmp_path / "other")
    with pytest.raises(tls.TlsConfigError, match="does not match"):
        tls.custom_files(tmp_path, {**env, "IRIS_TLS_KEY": str(other_key)})


def test_an_expired_custom_certificate_stops_the_start(tmp_path: Path) -> None:
    certfile, keyfile = _issue(tmp_path, days=-1)

    with pytest.raises(tls.TlsConfigError, match="expired"):
        tls.custom_files(tmp_path, {"IRIS_TLS_CERT": str(certfile), "IRIS_TLS_KEY": str(keyfile)})


def test_a_custom_certificate_defaults_to_the_data_tls_folder(tmp_path: Path) -> None:
    (tmp_path / "tls").mkdir()
    _issue(tmp_path / "tls")

    files = tls.custom_files(tmp_path, {})

    assert files == tls.TlsFiles(tmp_path / "tls" / "cert.pem", tmp_path / "tls" / "key.pem")


def test_the_launcher_prepares_a_certificate_only_when_asked(tmp_path: Path) -> None:
    assert serve.tls_files({"IRIS_DATA_DIR": str(tmp_path)}) is None

    files = serve.tls_files({"IRIS_DATA_DIR": str(tmp_path), "IRIS_TLS": "self", "IRIS_TLS_NAMES": "home.lan"})
    assert files == tls.TlsFiles(tmp_path / "tls" / tls.SELF_CERT_FILE, tmp_path / server_identity.KEY_FILE)

    with pytest.raises(tls.TlsConfigError):
        serve.tls_files({"IRIS_DATA_DIR": str(tmp_path), "IRIS_TLS": "custom"})


def test_devices_get_the_https_port_only_when_iris_serves_https() -> None:
    assert tls.device_https_port({}) is None
    assert tls.device_https_port({"IRIS_TLS": "self"}) == 8443
    assert tls.device_https_port({"IRIS_TLS": "custom", "IRIS_TLS_PUBLIC_PORT": "8503"}) == 8503


def test_the_pairing_code_sends_devices_to_https_while_the_page_stays_on_http() -> None:
    from core.pairing import device_addresses

    page = ["http://192.168.1.20:8501", "https://iris.example", "http://[fd00::1]:8501", "http://192.168.1.20:8501"]

    assert device_addresses(page, None) == page
    assert device_addresses(page, 8443) == ["https://192.168.1.20:8443", "https://iris.example", "https://[fd00::1]:8443"]
    assert device_addresses(["http://iris.lan:8501"], 443) == ["https://iris.lan"]


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_the_server_answers_browsers_over_http_and_devices_over_https(tmp_path: Path) -> None:
    import os
    import signal
    import subprocess
    import time
    import urllib.request

    root = Path(__file__).resolve().parents[1]
    http_port, https_port = _free_port(), _free_port()
    env = dict(
        os.environ, PYTHONPATH=str(root), IRIS_DATA_DIR=str(tmp_path / "data"), IRIS_TLS="self",
        IRIS_HTTP_PORT=str(http_port), IRIS_HTTPS_PORT=str(https_port), IRIS_LOAD_MODEL="0",
        IRIS_SERVER_MODE="private", IRIS_SESSION_HTTPS_ONLY="false",
    )
    process = subprocess.Popen(
        [sys.executable, str(root / "scripts" / "serve.py")], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                plain = urllib.request.urlopen(f"http://127.0.0.1:{http_port}/healthz", timeout=2).status
                secure = urllib.request.urlopen(f"https://127.0.0.1:{https_port}/healthz", timeout=2, context=insecure).status
                break
            except OSError:
                assert process.poll() is None, process.stdout.read() if process.stdout else ""
                assert time.monotonic() < deadline, "server did not come up"
                time.sleep(0.3)
        assert (plain, secure) == (200, 200)

        import socket as socket_module

        with socket_module.create_connection(("127.0.0.1", https_port)) as raw:
            with insecure.wrap_socket(raw, server_hostname="127.0.0.1") as wrapped:
                presented = x509.load_der_x509_certificate(wrapped.getpeercert(binary_form=True))
        # Devices meet the identity key they pinned.
        assert _spki(presented) == server_identity.read_identity(tmp_path / "data").public_key_der
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            # One signal stops both listeners after the app's shutdown ran; uvicorn
            # then re-raises the signal it caught, as it does on its own.
            assert process.wait(timeout=20) in (0, -signal.SIGTERM)
            output = process.stdout.read() if process.stdout else ""
            assert "Application shutdown complete." in output
        finally:
            if process.poll() is None:
                process.kill()
