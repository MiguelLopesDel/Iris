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


def test_the_launcher_adds_tls_only_when_asked(tmp_path: Path) -> None:
    plain = serve.uvicorn_args({"IRIS_DATA_DIR": str(tmp_path)})
    assert not any(arg.startswith("--ssl") for arg in plain)

    selfsigned = serve.uvicorn_args({"IRIS_DATA_DIR": str(tmp_path), "IRIS_TLS": "self", "IRIS_TLS_NAMES": "home.lan"})
    assert f"--ssl-keyfile={tmp_path / server_identity.KEY_FILE}" in selfsigned
    assert f"--ssl-certfile={tmp_path / 'tls' / tls.SELF_CERT_FILE}" in selfsigned

    with pytest.raises(tls.TlsConfigError):
        serve.uvicorn_args({"IRIS_DATA_DIR": str(tmp_path), "IRIS_TLS": "custom"})
