"""Tests for the persistent shared BrowserSession worker.

Exercise the thread-marshaling mechanics (submit runs on the worker thread,
results/exceptions come back, ops serialize, close stops it) with a fake context
-- no real browser.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

import core.browser_session as bs
from core.browser_session import BrowserSession


class _FakeContext:
    def __init__(self) -> None:
        self.closed = False


def _make_session() -> BrowserSession:
    ctx = _FakeContext()
    return BrowserSession(launcher=lambda: (ctx, lambda: setattr(ctx, "closed", True)))


def test_submit_runs_on_a_single_dedicated_worker_thread() -> None:
    session = _make_session()
    try:
        main = threading.get_ident()
        t1 = session.submit(lambda ctx: threading.get_ident())
        t2 = session.submit(lambda ctx: threading.get_ident())

        assert t1 != main  # ran off the caller's thread
        assert t1 == t2  # same dedicated worker thread every time
    finally:
        session.close()


def test_submit_returns_result_and_propagates_exceptions() -> None:
    session = _make_session()
    try:
        assert session.submit(lambda ctx: 21 * 2) == 42

        def boom(ctx):
            raise ValueError("falhou no worker")

        try:
            session.submit(boom)
            raise AssertionError("deveria ter propagado")
        except ValueError as exc:
            assert "falhou no worker" in str(exc)
    finally:
        session.close()


def test_submit_passes_the_shared_context() -> None:
    ctx = _FakeContext()
    session = BrowserSession(launcher=lambda: (ctx, lambda: None))
    try:
        got = session.submit(lambda c: c)
        assert got is ctx
    finally:
        session.close()


def test_operations_serialize_on_the_worker() -> None:
    session = _make_session()
    order: list[str] = []
    lock = threading.Lock()

    def op(tag: str):
        def run(ctx):
            with lock:
                order.append(f"start-{tag}")
            with lock:
                order.append(f"end-{tag}")
            return tag

        return run

    try:
        # Submitted from different threads, but the single worker runs them
        # one-at-a-time: no interleaving of start/end.
        results = []
        threads = [
            threading.Thread(target=lambda t=t: results.append(session.submit(op(t))))
            for t in ("a", "b", "c")
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # Every op's start is immediately followed by its own end.
        for i in range(0, len(order), 2):
            assert order[i].split("-")[1] == order[i + 1].split("-")[1]
        assert sorted(results) == ["a", "b", "c"]
    finally:
        session.close()


def test_hypr_hide_moves_window_to_special_workspace(monkeypatch) -> None:
    calls = []

    def fake_hyprctl(*args):
        calls.append(args)
        if args == ("-j", "clients"):
            return json.dumps([{"class": bs.WINDOW_CLASS, "address": "0xABC"}])
        return ""

    monkeypatch.setattr(bs, "_hyprctl", fake_hyprctl)

    assert bs._hypr_set_visible(False) is True
    assert (
        "dispatch",
        "movetoworkspacesilent",
        f"special:{bs._HYPR_SPECIAL},address:0xABC",
    ) in calls


def test_hypr_show_moves_to_active_workspace_and_focuses(monkeypatch) -> None:
    calls = []

    def fake_hyprctl(*args):
        calls.append(args)
        if args == ("-j", "clients"):
            return json.dumps([{"initialClass": bs.WINDOW_CLASS, "address": "0xABC"}])
        if args == ("-j", "activeworkspace"):
            return json.dumps({"id": 3})
        return ""

    monkeypatch.setattr(bs, "_hyprctl", fake_hyprctl)

    assert bs._hypr_set_visible(True) is True
    assert ("dispatch", "movetoworkspacesilent", "3,address:0xABC") in calls
    assert ("dispatch", "focuswindow", "address:0xABC") in calls


def test_kill_orphan_browsers_kills_matching_process() -> None:
    # A fake "chromium" carrying our window class in argv (an orphan from a crash).
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)",
         "chromium", f"--class={bs.WINDOW_CLASS}"]
    )
    try:
        time.sleep(0.3)  # let it show up in /proc
        assert proc.pid in bs._matching_browser_pids(bs.SHARED_PROFILE_DIR)

        killed = bs.kill_orphan_browsers(bs.SHARED_PROFILE_DIR)

        assert killed >= 1
        proc.wait(timeout=5)
        assert proc.poll() is not None  # the orphan is gone
    finally:
        if proc.poll() is None:
            proc.kill()


def test_matching_browser_pids_ignores_non_browser_processes() -> None:
    # A sleep without chrome/our-class in argv must NOT match.
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.3)
        assert proc.pid not in bs._matching_browser_pids(bs.SHARED_PROFILE_DIR)
    finally:
        proc.kill()


def test_hypr_address_none_when_class_absent(monkeypatch) -> None:
    monkeypatch.setattr(
        bs, "_hyprctl", lambda *a: json.dumps([{"class": "firefox", "address": "0x1"}])
    )
    assert bs._hypr_address(retries=1) is None


def test_close_stops_worker_and_runs_cleanup() -> None:
    ctx = _FakeContext()
    session = BrowserSession(launcher=lambda: (ctx, lambda: setattr(ctx, "closed", True)))
    assert session.alive() is True

    session.close()

    assert ctx.closed is True
    assert session.alive() is False
