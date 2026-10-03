"""Slows down password guessing on the login endpoints.

Each failed login for a username adds a wait before the next attempt for
that username is even checked: a few typos cost nothing, a guessing run soon
waits minutes per try. Throttling is per account, not per client address:
behind a reverse proxy every client has the proxy's address, and throttling
it would let one guesser slow everyone's login. Signed-in browsers and paired
devices keep working while an account is throttled.

Failures against existing accounts and against names that match no account
are kept apart. Only the second kind is bounded and evicted: otherwise a
guesser could fail against a real account, then spray made-up names until
the eviction dropped that account's delay, and guess again unslowed.

The state lives in memory. A restart clears it, which a guesser cannot
trigger, and one server process serves every login.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

FREE_ATTEMPTS = 5
BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 15 * 60.0
FORGET_AFTER_SECONDS = 24 * 60 * 60.0
MAX_TRACKED_UNKNOWN_NAMES = 10_000


class LoginThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        # normalized username -> (consecutive failures, time of the last failure).
        # Accounts are bounded by the users that exist and are never evicted.
        self._accounts: dict[str, tuple[int, float]] = {}
        # Names that match no account: unbounded input, so bounded here.
        self._unknown: dict[str, tuple[int, float]] = {}

    @staticmethod
    def _key(username: str) -> str:
        return username.strip().lower()

    @staticmethod
    def _delay(count: int) -> float:
        if count < FREE_ATTEMPTS:
            return 0.0
        return min(BASE_DELAY_SECONDS * 2 ** (count - FREE_ATTEMPTS), MAX_DELAY_SECONDS)

    def _table(self, known: bool) -> dict[str, tuple[int, float]]:
        return self._accounts if known else self._unknown

    def retry_after(self, username: str, *, known: bool) -> float:
        """Seconds to wait before an attempt for this username is checked; 0 when allowed.

        [known] says whether the name is an existing account. Unknown names are
        slowed the same way, so the answer does not reveal which names exist.
        """
        now = self._clock()
        key = self._key(username)
        with self._lock:
            table = self._table(known)
            count, last = table.get(key, (0, now))
            if now - last > FORGET_AFTER_SECONDS:
                table.pop(key, None)
                return 0.0
            return max(last + self._delay(count) - now, 0.0)

    def record_failure(self, username: str, *, known: bool) -> None:
        now = self._clock()
        key = self._key(username)
        with self._lock:
            table = self._table(known)
            count, _ = table.get(key, (0, now))
            table[key] = (count + 1, now)
            if not known and len(table) > MAX_TRACKED_UNKNOWN_NAMES:
                # Bound memory under a spray of made-up names: drop the oldest half.
                # Existing accounts live in the other table and are never dropped.
                oldest = sorted(table.items(), key=lambda item: item[1][1])
                for stale, _ in oldest[: MAX_TRACKED_UNKNOWN_NAMES // 2]:
                    table.pop(stale, None)

    def record_success(self, username: str) -> None:
        with self._lock:
            self._accounts.pop(self._key(username), None)
