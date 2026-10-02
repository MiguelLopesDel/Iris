"""Pairing a device with this server from a QR code or a link.

The pairing code is a link, ``iris://pair?v=1&id=...&u=...&ca=...``, carrying:

* ``id``  -- this installation's identifier, so a device can tell it is
  talking to the same server whatever address it uses;
* ``u``   -- one or more addresses the device may use, in order of preference;
* ``ca``  -- optionally, the SHA-256 fingerprint of the certificate authority
  the device should trust for this server. The certificate itself is served
  at ``/api/pairing/ca.pem``; the device accepts it only if its fingerprint
  matches the one in the code, which travelled over a channel the user trusts
  (the screen of a signed-in session).

None of this is secret: addresses, the identifier and a CA certificate are all
public by nature. The code grants no access by itself; signing in still does.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import segno

INSTANCE_ID_FILE = "instance_id"
CA_FILE = "pairing_ca.pem"
MAX_ADDRESSES = 8
SCHEME = "iris://pair"
VERSION = "1"

_PEM = re.compile(
    r"-----BEGIN CERTIFICATE-----\s*(?P<body>[A-Za-z0-9+/=\s]+?)\s*-----END CERTIFICATE-----"
)


class PairingError(ValueError):
    pass


def read_instance_id(data_dir: Path) -> str | None:
    """This installation's identifier, if it was created; never writes."""
    try:
        value = (data_dir / INSTANCE_ID_FILE).read_text().strip()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else None


def instance_id(data_dir: Path) -> str:
    """A stable random identifier for this installation, created on first use."""
    existing = read_instance_id(data_dir)
    if existing:
        return existing
    path = data_dir / INSTANCE_ID_FILE
    value = secrets.token_hex(16)
    data_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(value + "\n")
    os.replace(temporary, path)
    return value


def normalize_address(raw: str) -> str:
    """``scheme://host[:port]`` for an address devices can use, or :class:`PairingError`."""
    text = raw.strip()
    parts = urlsplit(text if "://" in text else f"http://{text}")
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise PairingError(f"endereço inválido: {raw!r} (use http://host:porta ou https://host)")
    if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username:
        raise PairingError(f"endereço inválido: {raw!r} (só esquema, host e porta)")
    try:
        port = parts.port
    except ValueError as exc:
        raise PairingError(f"porta inválida em {raw!r}") from exc
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = 443 if parts.scheme == "https" else 80
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != default else "")


def is_loopback(address: str) -> bool:
    """An address only this host can use (useless to a phone)."""
    host = urlsplit(address).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_addresses(raw: str) -> list[str]:
    """Addresses separated by spaces, commas or lines; duplicates dropped, order kept."""
    addresses: list[str] = []
    for item in re.split(r"[\s,]+", raw.strip()):
        if item:
            address = normalize_address(item)
            if address not in addresses:
                addresses.append(address)
    if len(addresses) > MAX_ADDRESSES:
        raise PairingError(f"no máximo {MAX_ADDRESSES} endereços")
    return addresses


def pairing_addresses(configured: list[str], current: str | None) -> list[str]:
    """The browser's current address first (if a phone could use it), then the configured ones."""
    addresses: list[str] = []
    if current:
        try:
            normalized = normalize_address(current)
        except PairingError:
            normalized = None
        if normalized and not is_loopback(normalized):
            addresses.append(normalized)
    for address in configured:
        if address not in addresses and not is_loopback(address):
            addresses.append(address)
    return addresses[:MAX_ADDRESSES]


def certificate_der(pem: str) -> bytes:
    """The DER bytes of the single certificate in ``pem``, or :class:`PairingError`."""
    blocks = list(_PEM.finditer(pem))
    if len(blocks) != 1:
        raise PairingError("envie exatamente um certificado PEM (-----BEGIN CERTIFICATE-----)")
    try:
        der = base64.b64decode("".join(blocks[0].group("body").split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PairingError("o certificado não está em base64 válido") from exc
    # An X.509 certificate is a DER SEQUENCE; anything else is not one.
    if len(der) < 64 or der[0] != 0x30:
        raise PairingError("o conteúdo não é um certificado X.509")
    return der


def fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def save_ca(data_dir: Path, pem: str) -> str:
    der = certificate_der(pem)
    text = "-----BEGIN CERTIFICATE-----\n"
    encoded = base64.b64encode(der).decode()
    text += "\n".join(encoded[i:i + 64] for i in range(0, len(encoded), 64))
    text += "\n-----END CERTIFICATE-----\n"
    path = data_dir / CA_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)
    return fingerprint(der)


def load_ca(data_dir: Path) -> str | None:
    path = data_dir / CA_FILE
    return path.read_text() if path.is_file() else None


def ca_fingerprint(data_dir: Path) -> str | None:
    pem = load_ca(data_dir)
    if pem is None:
        return None
    try:
        return fingerprint(certificate_der(pem))
    except PairingError:
        return None


def remove_ca(data_dir: Path) -> None:
    (data_dir / CA_FILE).unlink(missing_ok=True)


def pairing_uri(instance: str, addresses: list[str], ca_sha256: str | None) -> str:
    query = [("v", VERSION), ("id", instance)] + [("u", address) for address in addresses]
    if ca_sha256:
        query.append(("ca", ca_sha256))
    return f"{SCHEME}?{urlencode(query)}"


def qr_svg(text: str) -> str:
    """An SVG QR code with a light quiet zone, readable on dark and light pages alike."""
    return segno.make(text, error="m").svg_inline(scale=6, border=4, dark="#000", light="#fff", omitsize=True)
