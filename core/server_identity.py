"""This installation's cryptographic identity, which a paired device verifies.

The ``instance_id`` tells installations apart, but it is public: any machine
can repeat it. A device that pairs from a QR code also receives the SHA-256
fingerprint of this installation's public key (``k=`` in the pairing code).
Before it sends a token or a password, it asks the server to sign a fresh
challenge (``GET /api/identity``) and checks the signature against that key.
A machine that took over the address cannot answer: it does not hold the
private key.

The key is ECDSA P-256 because it also serves as the key of the self-signed
TLS certificate (``IRIS_TLS=self``) and Android accepts P-256 certificates on
every supported version. It lives in the data directory, readable only by
the server's user, and survives restarts and upgrades. Losing it (a new data
directory) makes this a new server to every paired device, which then has
to pair again, as with a new ``instance_id``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

KEY_FILE = "identity_key.pem"
ALGORITHM = "ecdsa-p256-sha256"
DOMAIN = "iris-identity-v1"

# What a device may send as a challenge: long enough to be unguessable, short
# and plain enough to be harmless in a signed message and in logs.
_NONCE = re.compile(r"[A-Za-z0-9_-]{16,128}")
_ADDRESS = re.compile(r"https?://[A-Za-z0-9.\-\[\]:]{1,255}")


class IdentityError(ValueError):
    pass


@dataclass(frozen=True)
class ServerIdentity:
    private_key: ec.EllipticCurvePrivateKey

    @property
    def public_key_der(self) -> bytes:
        """The public key as SubjectPublicKeyInfo, the form TLS libraries pin."""
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the public key, in lowercase hex: what the pairing code carries."""
        return hashlib.sha256(self.public_key_der).hexdigest()

    def sign(self, message: bytes) -> bytes:
        return self.private_key.sign(message, ec.ECDSA(hashes.SHA256()))


def challenge_message(instance_id: str, nonce: str, address: str) -> bytes:
    """What the server signs for a challenge; a device rebuilds it to verify.

    The installation, the device's nonce and the address the device used are
    all in it, so a signature cannot be replayed for another challenge,
    another installation or another address.
    """
    return "\n".join((DOMAIN, instance_id, nonce, address)).encode()


def validate_challenge(nonce: str, address: str) -> None:
    if not _NONCE.fullmatch(nonce):
        raise IdentityError("nonce must be 16 to 128 characters of [A-Za-z0-9_-]")
    if not _ADDRESS.fullmatch(address):
        raise IdentityError("address must be the scheme, host and port the device used")


def answer_challenge(identity: ServerIdentity, instance_id: str, nonce: str, address: str) -> dict:
    """The response to ``GET /api/identity``: the public key and a signed challenge."""
    validate_challenge(nonce, address)
    signature = identity.sign(challenge_message(instance_id, nonce, address))
    return {
        "version": 1,
        "algorithm": ALGORITHM,
        "instance_id": instance_id,
        "public_key": base64.b64encode(identity.public_key_der).decode(),
        "key_sha256": identity.fingerprint,
        "signature": base64.b64encode(signature).decode(),
    }


def read_identity(data_dir: Path) -> ServerIdentity | None:
    """This installation's identity, if it was created; never writes."""
    try:
        pem = (data_dir / KEY_FILE).read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise IdentityError(f"{KEY_FILE} is not an ECDSA P-256 key")
    return ServerIdentity(key)


def server_identity(data_dir: Path) -> ServerIdentity:
    """This installation's identity, created on first use and kept from then on."""
    existing = read_identity(data_dir)
    if existing is not None:
        return existing
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / KEY_FILE
    temporary = path.with_suffix(".tmp")
    # Created private from the first byte: never readable by others, even briefly.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return ServerIdentity(key)
