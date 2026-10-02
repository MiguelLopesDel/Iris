"""Slows down password guessing on the login endpoints.

Each failed login for a username adds a wait before the next attempt for
that username is even checked: a few typos cost nothing, a guessing run soon
waits minutes per try. Throttling is per account, not per client address:
behind a reverse proxy every client has the proxy's address, and throttling
it would let one guesser slow everyone's login. Signed-in browsers and paired
devices keep working while an account is throttled.

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
MAX_TRACKED_ACCOUNTS = 10_000


class LoginThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        # normalized username -> (consecutive failures, time of the last failure)
        self._failures: dict[str, tuple[int, float]] = {}

    @staticmethod
    def _key(username: str) -> str:
        return username.strip().lower()

    @staticmethod
    def _delay(count: int) -> float:
        if count < FREE_ATTEMPTS:
            return 0.0
        return min(BASE_DELAY_SECONDS * 2 ** (count - FREE_ATTEMPTS), MAX_DELAY_SECONDS)

    def retry_after(self, username: str) -> float:
        """Seconds to wait before an attempt for this username is checked; 0 when allowed."""
        now = self._clock()
        key = self._key(username)
        with self._lock:
            count, last = self._failures.get(key, (0, now))
            if now - last > FORGET_AFTER_SECONDS:
                self._failures.pop(key, None)
                return 0.0
            return max(last + self._delay(count) - now, 0.0)

    def record_failure(self, username: str) -> None:
        now = self._clock()
        key = self._key(username)
        with self._lock:
            count, _ = self._failures.get(key, (0, now))
            self._failures[key] = (count + 1, now)
            if len(self._failures) > MAX_TRACKED_ACCOUNTS:
                # Bound memory under a spray of made-up usernames: drop the oldest half.
                oldest = sorted(self._failures.items(), key=lambda item: item[1][1])
                for stale, _ in oldest[: MAX_TRACKED_ACCOUNTS // 2]:
                    self._failures.pop(stale, None)

    def record_success(self, username: str) -> None:
        with self._lock:
            self._failures.pop(self._key(username), None)
