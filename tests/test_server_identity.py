"""The installation key a paired device verifies before it sends credentials."""

from __future__ import annotations

import base64
import hashlib
import stat
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from core import server_identity
from core.pairing import pairing_uri


def verify_identity(answer: dict, nonce: str, address: str) -> bool:
    """What a device does with an answer: rebuild the message and check the signature."""
    public_der = base64.b64decode(answer["public_key"])
    if hashlib.sha256(public_der).hexdigest() != answer["key_sha256"]:
        return False
    key = serialization.load_der_public_key(public_der)
    message = server_identity.challenge_message(answer["instance_id"], nonce, address)
    try:
        key.verify(base64.b64decode(answer["signature"]), message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def test_the_key_is_created_once_private_and_kept(tmp_path: Path) -> None:
    first = server_identity.server_identity(tmp_path)
    again = server_identity.server_identity(tmp_path)

    assert again.fingerprint == first.fingerprint
    assert stat.S_IMODE((tmp_path / server_identity.KEY_FILE).stat().st_mode) == 0o600
    assert server_identity.read_identity(tmp_path).fingerprint == first.fingerprint


def test_the_short_code_is_the_start_of_the_fingerprint_in_groups(tmp_path: Path) -> None:
    assert server_identity.short_code("4f2a91c30b7de215" + "0" * 48) == "4F2A-91C3-0B7D"
    identity = server_identity.server_identity(tmp_path)
    assert identity.short_code == server_identity.short_code(identity.fingerprint)


def test_reading_never_creates_a_key(tmp_path: Path) -> None:
    assert server_identity.read_identity(tmp_path) is None
    assert not (tmp_path / server_identity.KEY_FILE).exists()


def test_a_challenge_is_signed_for_that_nonce_installation_and_address(tmp_path: Path) -> None:
    identity = server_identity.server_identity(tmp_path)
    answer = server_identity.answer_challenge(identity, "a" * 32, "n" * 32, "https://iris.example")

    assert verify_identity(answer, "n" * 32, "https://iris.example")
    # The same signature proves nothing for another challenge or another address.
    assert not verify_identity(answer, "m" * 32, "https://iris.example")
    assert not verify_identity(answer, "n" * 32, "https://other.example")


def test_another_installation_cannot_answer_for_this_one(tmp_path: Path) -> None:
    ours = server_identity.server_identity(tmp_path / "ours")
    impostor = server_identity.server_identity(tmp_path / "impostor")
    answer = server_identity.answer_challenge(impostor, "a" * 32, "n" * 32, "http://192.168.1.20:8501")

    # It signs correctly with its own key, but that key is not the one the device pinned.
    assert verify_identity(answer, "n" * 32, "http://192.168.1.20:8501")
    assert answer["key_sha256"] != ours.fingerprint


@pytest.mark.parametrize(
    ("nonce", "address"),
    [
        ("short", "https://iris.example"),
        ("n" * 32 + "\n", "https://iris.example"),
        ("n" * 32, "iris.example"),
        ("n" * 32, "https://iris.example/path"),
    ],
)
def test_malformed_challenges_are_refused(tmp_path: Path, nonce: str, address: str) -> None:
    identity = server_identity.server_identity(tmp_path)
    with pytest.raises(server_identity.IdentityError):
        server_identity.answer_challenge(identity, "a" * 32, nonce, address)


def test_the_pairing_code_carries_the_key_fingerprint(tmp_path: Path) -> None:
    key = server_identity.server_identity(tmp_path).fingerprint

    uri = pairing_uri("a" * 32, ["http://192.168.1.20:8501"], None, key)

    assert uri.endswith(f"&k={key}")
