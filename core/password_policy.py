"""What a password must be, and what we only recommend.

Following NIST SP 800-63B: a minimum length, no composition rules (symbols,
digits, mixed case), and a check against passwords known to be common. A
longer password is recommended but never required: forcing 12 characters or a
generator pushed people to write passwords down or reuse one they knew. Login
attempts are rate limited (:mod:`core.login_throttle`), which is what makes a
shorter minimum acceptable.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

MIN_LENGTH = 8
RECOMMENDED_LENGTH = 12
_COMMON_PASSWORDS = Path(__file__).parent / "data" / "common_passwords.txt"


class PasswordRejected(ValueError):
    pass


@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    lines = _COMMON_PASSWORDS.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def check_length(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordRejected(f"A senha precisa ter pelo menos {MIN_LENGTH} caracteres")


def check_new_password(password: str, username: str | None = None) -> None:
    """Raises :class:`PasswordRejected` with a message for the person choosing it."""
    check_length(password)
    lowered = password.strip().lower()
    if lowered in _common_passwords():
        raise PasswordRejected("Essa senha é uma das mais usadas e fácil de adivinhar; escolha outra")
    if username and lowered == username.strip().lower():
        raise PasswordRejected("A senha não pode ser igual ao nome de usuário")
    if len(set(lowered)) == 1:
        raise PasswordRejected("A senha não pode ser um único caractere repetido")


def recommendations(password: str) -> list[str]:
    """Advice that never blocks: shown next to an accepted password."""
    if len(password) < RECOMMENDED_LENGTH:
        return [
            f"Recomendamos {RECOMMENDED_LENGTH} caracteres ou mais; uma frase curta "
            "é fácil de lembrar e difícil de adivinhar."
        ]
    return []
