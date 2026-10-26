"""HTTPS served by Iris itself, in the mode the administrator chooses (``IRIS_TLS``).

The web interface always stays on plain HTTP (port 8501), so setting up and
using Iris in a browser never shows a certificate warning. The modes add an
HTTPS listener (port 8443) for devices:

* ``off`` (the default): no HTTPS. TLS, if any, is done in front of Iris by
  a proxy or a mesh (``tailscale serve``, Caddy, nginx), and the pairing code
  offers that service's ``https://`` address.
* ``self``: a certificate for this installation's identity key
  (:mod:`core.server_identity`), issued by a local authority the installation
  creates for itself. A paired app pins the key, not the authority, so the
  certificate can be reissued (new addresses) without pairing again. A
  computer trusts the authority once (``/api/tls/ca.crt``, Sistema tab) and
  browsers then accept the certificate. The pairing code turns the
  ``http://`` addresses into this ``https://`` one.
* ``custom``: the administrator's own certificate and key, for example from a
  public or company CA. Paired devices still check the identity key through
  ``/api/identity``.

The certificate lives under ``<data>/tls``, as a chain (the certificate, then
the authority) so a proxy in front can serve the same file; a custom one is read from
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
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from core import server_identity

MODES = ("off", "self", "custom")
TLS_DIR = "tls"
# The name predates the local authority; proxies mount this path.
SELF_CERT_FILE = "self-signed.crt"
LOCAL_CA_CERT_FILE = "local-ca.crt"
LOCAL_CA_KEY_FILE = "local-ca.key"

# The authority is what computers trust, so it lives long: replacing it means
# trusting it again on each of them.
LOCAL_CA_VALIDITY = dt.timedelta(days=3650)
# The certificate is reissued on its own, and devices pin its key, not its
# dates. Apple refuses server certificates valid for more than 825 days, even
# under an authority the person added.
SELF_VALIDITY = dt.timedelta(days=825)
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


def device_https_port(environ: dict[str, str] | None = None) -> int | None:
    """The host port devices reach HTTPS at, or None when Iris serves no HTTPS."""
    env = environ if environ is not None else os.environ
    try:
        if mode_from_env(env) == "off":
            return None
    except TlsConfigError:
        return None
    return int(env.get("IRIS_TLS_PUBLIC_PORT") or env.get("IRIS_HTTPS_PORT") or 8443)


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


def _name_constraints(names: list[str]) -> x509.NameConstraints:
    subtrees: list[x509.GeneralName] = []
    for name in names:
        try:
            address = ipaddress.ip_address(name)
        except ValueError:
            subtrees.append(x509.DNSName(name))
        else:
            subtrees.append(x509.IPAddress(ipaddress.ip_network(address)))
    return x509.NameConstraints(permitted_subtrees=subtrees, excluded_subtrees=None)


def constrained_names(authority: x509.Certificate) -> set[str]:
    try:
        constraints = authority.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return set()
    names: set[str] = set()
    for subtree in constraints.permitted_subtrees or ():
        if isinstance(subtree, x509.IPAddress):
            names.add(str(subtree.value.network_address))
        else:
            names.add(str(subtree.value).lower())
    return names


@dataclass(frozen=True)
class LocalAuthority:
    certificate: x509.Certificate
    private_key: ec.EllipticCurvePrivateKey

    @property
    def pem(self) -> bytes:
        return self.certificate.public_bytes(serialization.Encoding.PEM)

    @property
    def sha256(self) -> str:
        """The certificate's SHA-256, as browsers and system settings show it."""
        return self.certificate.fingerprint(hashes.SHA256()).hex()


def read_local_authority(data_dir: Path) -> LocalAuthority | None:
    """The local authority, if this installation made one; never writes."""
    try:
        certificate = x509.load_pem_x509_certificate((data_dir / TLS_DIR / LOCAL_CA_CERT_FILE).read_bytes())
        key = serialization.load_pem_private_key(
            (data_dir / TLS_DIR / LOCAL_CA_KEY_FILE).read_bytes(), password=None
        )
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or _spki_of(key) != _spki_of(certificate):
        return None
    return LocalAuthority(certificate, key)


def _spki_of(holder: x509.Certificate | ec.EllipticCurvePrivateKey) -> bytes:
    return holder.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def ensure_local_authority(data_dir: Path, names: list[str], now: dt.datetime | None = None) -> LocalAuthority:
    """The authority that issues this installation's certificate, made again only when needed.

    It may only certify ``names`` (name constraints), so a computer that
    trusts it trusts nothing else: a copy of its key cannot impersonate other
    sites. A new name therefore needs a new authority, with a new key, which
    each computer trusts again.
    """
    now = now or dt.datetime.now(dt.UTC)
    current = read_local_authority(data_dir)
    if (
        current is not None
        and constrained_names(current.certificate) == set(names)
        and current.certificate.not_valid_after_utc - now > RENEW_BEFORE
    ):
        return current

    identity = server_identity.server_identity(data_dir)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"Iris {identity.short_code} (autoridade local)"),
    ])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + LOCAL_CA_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(_name_constraints(names), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    folder = data_dir / TLS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    key_temporary = folder / f"{LOCAL_CA_KEY_FILE}.tmp"
    # Readable only by the server's user, like the identity key.
    descriptor = os.open(key_temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ))
    cert_temporary = folder / f"{LOCAL_CA_CERT_FILE}.tmp"
    cert_temporary.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    os.replace(key_temporary, folder / LOCAL_CA_KEY_FILE)
    os.replace(cert_temporary, folder / LOCAL_CA_CERT_FILE)
    return LocalAuthority(certificate, key)


def _issued_by(certificate: x509.Certificate, authority: x509.Certificate) -> bool:
    if certificate.issuer != authority.subject:
        return False
    try:
        certificate.verify_directly_issued_by(authority)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def ensure_self_signed(data_dir: Path, names: list[str], now: dt.datetime | None = None) -> TlsFiles:
    """This installation's certificate, issued again only when needed.

    It certifies the identity key, so its public key is the one paired
    devices pinned, and it is issued by the local authority
    (:func:`ensure_local_authority`) that computers can trust. A new
    certificate is issued when the names or the authority change, or when it
    nears its end; the key never changes. The file holds the certificate and
    then the authority, the chain a TLS server sends.
    """
    now = now or dt.datetime.now(dt.UTC)
    identity = server_identity.server_identity(data_dir)
    authority = ensure_local_authority(data_dir, names, now)
    keyfile = data_dir / server_identity.KEY_FILE
    certfile = data_dir / TLS_DIR / SELF_CERT_FILE
    if certfile.exists():
        current = x509.load_pem_x509_certificates(certfile.read_bytes())[0]
        if (
            _spki_of(current) == identity.public_key_der
            and _names_of(current) == set(names)
            and _issued_by(current, authority.certificate)
            and current.not_valid_after_utc - now > RENEW_BEFORE
        ):
            return TlsFiles(certfile, keyfile)

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Iris {identity.fingerprint[:16]}")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(authority.certificate.subject)
        .public_key(identity.private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(min(now + SELF_VALIDITY, authority.certificate.not_valid_after_utc))
        .add_extension(_subject_alternative_names(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(authority.private_key.public_key()),
            critical=False,
        )
        .sign(authority.private_key, hashes.SHA256())
    )
    certfile.parent.mkdir(parents=True, exist_ok=True)
    temporary = certfile.with_suffix(".tmp")
    temporary.write_bytes(certificate.public_bytes(serialization.Encoding.PEM) + authority.pem)
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
