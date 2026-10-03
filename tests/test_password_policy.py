"""Password rules: a short minimum, common passwords refused, and login guessing slowed."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.auth import hash_password
from core.login_throttle import (
    FREE_ATTEMPTS,
    MAX_DELAY_SECONDS,
    MAX_TRACKED_UNKNOWN_NAMES,
    LoginThrottle,
)
from core.password_policy import (
    MIN_LENGTH,
    PasswordRejected,
    check_new_password,
    recommendations,
)


def test_eight_characters_are_enough_without_composition_rules():
    check_new_password("gato azul")
    check_new_password("abcdwxyz")
    hash_password("x" * 7 + "y")


def test_shorter_than_the_minimum_is_refused():
    with pytest.raises(PasswordRejected, match=f"pelo menos {MIN_LENGTH}"):
        check_new_password("curta")
    with pytest.raises(ValueError):
        hash_password("1234567")


@pytest.mark.parametrize("password", ["12345678", "Password", "qwertyuiop", "senha123", "Flamengo"])
def test_common_passwords_are_refused_whatever_the_case(password):
    with pytest.raises(PasswordRejected, match="mais usadas"):
        check_new_password(password)


def test_the_username_and_a_repeated_character_are_refused():
    with pytest.raises(PasswordRejected, match="nome de usuário"):
        check_new_password("Miguel123", username="miguel123")
    with pytest.raises(PasswordRejected, match="repetido"):
        check_new_password("zzzzzzzzzz")


def test_a_short_password_gets_advice_but_is_accepted():
    check_new_password("lua cheia")
    assert recommendations("lua cheia")
    assert recommendations("a lua cheia no mar") == []


class _Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def test_a_few_typos_cost_nothing():
    throttle = LoginThrottle(clock=_Clock())
    for _ in range(FREE_ATTEMPTS - 1):
        throttle.record_failure("alice", known=True)
    assert throttle.retry_after("alice", known=True) == 0


def test_repeated_failures_wait_longer_each_time_up_to_a_cap():
    clock = _Clock()
    throttle = LoginThrottle(clock=clock)
    waits = []
    for _ in range(FREE_ATTEMPTS + 12):
        throttle.record_failure("Alice", known=True)
        waits.append(throttle.retry_after("alice", known=True))
    blocked = [wait for wait in waits if wait > 0]
    assert blocked == sorted(blocked)
    assert blocked[-1] == MAX_DELAY_SECONDS
    clock.now += MAX_DELAY_SECONDS
    assert throttle.retry_after("alice", known=True) == 0


def test_a_success_clears_the_account_and_other_accounts_are_unaffected():
    throttle = LoginThrottle(clock=_Clock())
    for _ in range(FREE_ATTEMPTS + 2):
        throttle.record_failure("alice", known=True)
    assert throttle.retry_after("alice", known=True) > 0
    assert throttle.retry_after("bob", known=True) == 0
    throttle.record_success("alice")
    assert throttle.retry_after("alice", known=True) == 0


def test_a_spray_of_made_up_names_does_not_clear_a_real_account():
    throttle = LoginThrottle(clock=_Clock())
    for _ in range(FREE_ATTEMPTS + 1):
        throttle.record_failure("admin", known=True)
    blocked = throttle.retry_after("admin", known=True)
    assert blocked > 0

    for index in range(MAX_TRACKED_UNKNOWN_NAMES + 10):
        throttle.record_failure(f"made-up-{index}", known=False)

    assert throttle.retry_after("admin", known=True) == blocked


def test_unknown_names_are_slowed_like_accounts_so_answers_do_not_reveal_which_exist():
    throttle = LoginThrottle(clock=_Clock())
    for _ in range(FREE_ATTEMPTS + 1):
        throttle.record_failure("nobody", known=False)
        throttle.record_failure("admin", known=True)
    assert throttle.retry_after("nobody", known=False) == throttle.retry_after("admin", known=True) > 0


def test_login_endpoints_throttle_guessing_and_new_accounts_refuse_common_passwords(tmp_path: Path):
    script = r'''
from pathlib import Path
from fastapi.testclient import TestClient
from core.auth import hash_password
from core.indexer_db import init_db
from core.login_throttle import FREE_ATTEMPTS
from core.users_db import create_user

data = Path("data")
admin = create_user(data / "users.db", data, username="admin", password_hash=hash_password("lua cheia"), is_admin=True)
init_db(admin.db_path).close()

import server
with TestClient(server.app) as client:
    for _ in range(FREE_ATTEMPTS):
        assert client.post("/api/auth/login", data={"username": "admin", "password": "errada!!"}).status_code == 401
    blocked = client.post("/api/auth/login", data={"username": "admin", "password": "lua cheia"})
    assert blocked.status_code == 429, blocked.text
    assert int(blocked.headers["Retry-After"]) > 0
    assert "Tente de novo em" in blocked.json()["detail"]
    # The device login shares the account's throttle.
    device = client.post("/api/auth/devices/login", data={"username": "ADMIN", "password": "lua cheia", "device_name": "x"})
    assert device.status_code == 429
    # Another account is not slowed by guesses against this one.
    assert client.post("/api/auth/login", data={"username": "bob", "password": "qualquer"}).status_code == 401

    server.app.state.login_throttle.record_success("admin")
    assert client.post("/api/auth/login", data={"username": "admin", "password": "lua cheia"}).status_code == 200
    common = client.post("/api/auth/users", data={"username": "carol", "password": "12345678"})
    assert common.status_code == 400 and "mais usadas" in common.json()["detail"]
    assert client.post("/api/auth/users", data={"username": "carol", "password": "sol de verão"}).status_code == 200
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), IRIS_LOAD_MODEL="0")
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
