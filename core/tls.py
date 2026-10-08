"""HTTPS served by Iris itself, in the mode the administrator chooses (``IRIS_TLS``).

* ``off`` (the default): plain HTTP. TLS, if any, is done in front of Iris by
  a proxy or a mesh (``tailscale serve``, Caddy, nginx), and the pairing code
  offers that service's ``https://`` address.
* ``self``: a self-signed certificate made from this installation's identity
  key (:mod:`core.server_identity`). A paired app pins that key, so the
  certificate can be reissued (new addresses) without pairing again. Browsers
  warn about it: nothing public vouches for it.
* ``custom``: the administrator's own certificate and key, for example from a
  public or company CA, so browsers accept it. Paired devices still check the
  identity key through ``/api/identity``.

The certificate lives under ``<data>/tls``; a custom one is read from
``IRIS_TLS_CERT`` / ``IRIS_TLS_KEY`` (by default ``<data>/tls/cert.pem`` and
``<data>/tls/key.pem``).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from core import server_identity

MODES = ("off", "self", "custom")
TLS_DIR = "tls"
SELF_CERT_FILE = "self-signed.crt"

# A pinned key does not expire, and nothing public checks this certificate's
# dates; a long life spares the administrator renewals that would change nothing.
SELF_VALIDITY = dt.timedelta(days=3650)
RENEW_BEFORE = dt.timedelta(days=30)


class TlsConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TlsFiles:
    certfile: Path
    keyfile: Path


def mode_from_env(environ: dict[str, str] | None = None) -> str:
    value = (environ if environ is not None else os.environ).get("IRIS_TLS", "off").strip().lower() or "off"
    if value not in MODES:
        raise TlsConfigError(f"IRIS_TLS={value!r}: use one of {', '.join(MODES)}")
    return value


def certificate_names(addresses: list[str], extra: str = "") -> list[str]:
    """The hosts a certificate should name: the pairing addresses, extras, and loopback."""
    names: list[str] = []
    for address in addresses:
        host = urlsplit(address if "://" in address else f"https://{address}").hostname
        if host:
            names.append(host)
    names.extend(extra.split())
    names.extend(("localhost", "127.0.0.1"))
    seen: dict[str, None] = {}
    for name in names:
        seen.setdefault(name.strip().lower(), None)
    return [name for name in seen if name]


def _subject_alternative_names(names: list[str]) -> x509.SubjectAlternativeName:
    entries: list[x509.GeneralName] = []
    for name in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            entries.append(x509.DNSName(name))
    return x509.SubjectAlternativeName(entries)


def _names_of(certificate: x509.Certificate) -> set[str]:
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set()
    return {str(value).lower() for value in san.get_values_for_type(x509.DNSName)} | {
        str(value) for value in san.get_values_for_type(x509.IPAddress)
    }


def ensure_self_signed(data_dir: Path, names: list[str], now: dt.datetime | None = None) -> TlsFiles:
    """The self-signed certificate for this installation, issued again only when needed.

    It is signed by, and certifies, the identity key, so its public key is the
    one paired devices pinned. A new certificate is issued when the names
    change or the current one nears its end; the key never changes.
    """
    now = now or dt.datetime.now(dt.UTC)
    identity = server_identity.server_identity(data_dir)
    keyfile = data_dir / server_identity.KEY_FILE
    certfile = data_dir / TLS_DIR / SELF_CERT_FILE
    if certfile.exists():
        current = x509.load_pem_x509_certificate(certfile.read_bytes())
        same_key = current.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ) == identity.public_key_der
        if same_key and _names_of(current) == set(names) and current.not_valid_after_utc - now > RENEW_BEFORE:
            return TlsFiles(certfile, keyfile)

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Iris {identity.fingerprint[:16]}")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(identity.private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + SELF_VALIDITY)
        .add_extension(_subject_alternative_names(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(identity.private_key, hashes.SHA256())
    )
    certfile.parent.mkdir(parents=True, exist_ok=True)
    temporary = certfile.with_suffix(".tmp")
    temporary.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    os.replace(temporary, certfile)
    return TlsFiles(certfile, keyfile)


def custom_files(data_dir: Path, environ: dict[str, str] | None = None) -> TlsFiles:
    """The administrator's certificate and key, checked before the server starts.

    A missing file, an expired certificate, or a key that does not match
    stops the start with a message that says which, instead of a server
    that answers nothing.
    """
    env = environ if environ is not None else os.environ
    certfile = Path(env.get("IRIS_TLS_CERT") or data_dir / TLS_DIR / "cert.pem")
    keyfile = Path(env.get("IRIS_TLS_KEY") or data_dir / TLS_DIR / "key.pem")
    for path, what in ((certfile, "IRIS_TLS_CERT"), (keyfile, "IRIS_TLS_KEY")):
        if not path.is_file():
            raise TlsConfigError(f"IRIS_TLS=custom: {what} not found at {path}")
    try:
        chain = x509.load_pem_x509_certificates(certfile.read_bytes())
        key = serialization.load_pem_private_key(keyfile.read_bytes(), password=None)
    except ValueError as exc:
        raise TlsConfigError(f"IRIS_TLS=custom: unreadable certificate or key ({exc})") from exc
    leaf = chain[0]
    if leaf.not_valid_after_utc <= dt.datetime.now(dt.UTC):
        raise TlsConfigError(f"IRIS_TLS=custom: the certificate in {certfile} has expired")
    der = serialization.Encoding.DER
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    if leaf.public_key().public_bytes(der, spki) != key.public_key().public_bytes(der, spki):
        raise TlsConfigError(f"IRIS_TLS=custom: the key in {keyfile} does not match the certificate")
    return TlsFiles(certfile, keyfile)
