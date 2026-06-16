"""Persistent, shared browser session for the web-enrichment pipeline.

Playwright's *sync* API is thread-bound: a browser created on one thread cannot
be driven from another. Each enrichment job runs on its own thread, so to keep a
single browser alive **across jobs** (instead of opening/closing Chromium every
time) we run it on one dedicated worker thread and marshal all browser work to
that thread via a queue. Callers submit ``fn(context)`` and block for the result.

The window starts minimized (effectively hidden) and is restored only when a
human is needed (login / CAPTCHA), via the CDP ``Browser.setWindowBounds`` command.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

# Reuse the profile the web-chat backend already logged into, so the shared
# browser keeps the existing ChatGPT login (and Lens cookies live here too).
SHARED_PROFILE_DIR = os.environ.get(
    "IRIS_BROWSER_PROFILE_DIR", os.path.expanduser("~/.iris/webchat-profile")
)
# Unique window class so a Wayland compositor (Hyprland) can target this window.
WINDOW_CLASS = os.environ.get("IRIS_BROWSER_WINDOW_CLASS", "iris-meme-browser")
_HYPR_SPECIAL = "iris"
_LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    f"--class={WINDOW_CLASS}",
)


def shared_session_enabled() -> bool:
    """Whether browser work should reuse the persistent shared session."""
    return os.environ.get("IRIS_BROWSER_SHARED", "1").strip().lower() not in {"0", "false", "no"}


# ── Window show/hide ─────────────────────────────────────────────────────────
# Wayland clients can't move/minimize themselves -- the compositor does. So on
# Hyprland we drive it via `hyprctl` (scratchpad/special workspace); on X11 we
# use the CDP Browser.setWindowBounds command. Both best-effort.


def _hyprland_active() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and shutil.which("hyprctl") is not None


def _hyprctl(*args: str) -> str:
    return subprocess.run(
        ["hyprctl", *args], capture_output=True, text=True, timeout=5
    ).stdout


def _hypr_address(retries: int = 10) -> str | None:
    """Find the address of our browser window (by class), retrying while it maps."""
    for _ in range(retries):
        try:
            clients = json.loads(_hyprctl("-j", "clients"))
            for client in clients:
                if WINDOW_CLASS in (client.get("class", ""), client.get("initialClass", "")):
                    return client.get("address")
        except Exception:
            pass
        time.sleep(0.2)
    return None


def _hypr_set_visible(visible: bool) -> bool:
    address = _hypr_address()
    if not address:
        return False
    try:
        if visible:
            ws = json.loads(_hyprctl("-j", "activeworkspace")).get("id")
            _hyprctl("dispatch", "movetoworkspacesilent", f"{ws},address:{address}")
            _hyprctl("dispatch", "focuswindow", f"address:{address}")
        else:
            _hyprctl("dispatch", "movetoworkspacesilent", f"special:{_HYPR_SPECIAL},address:{address}")
        return True
    except Exception:
        return False


def set_window_visible(page: Any, visible: bool) -> None:
    """Show (restore/foreground) or hide (minimize/scratchpad) the browser window.
    Best-effort; must run on the worker thread. Tries Hyprland first, then CDP."""
    if _hyprland_active() and _hypr_set_visible(visible):
        return
    try:
        cdp = page.context.new_cdp_session(page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
        state = "normal" if visible else "minimized"
        cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": state}})
        if visible:
            try:
                page.bring_to_front()
            except Exception:
                pass
    except Exception:
        pass


class BrowserSession:
    """A headed browser living on a dedicated thread, reused across jobs.

    ``submit(fn)`` runs ``fn(context)`` on the worker thread and returns its
    result. Because there is a single worker, concurrent jobs serialize naturally
    (no two jobs drive the browser at once). For tests, pass ``launcher`` to skip
    Playwright entirely and supply a fake context.
    """

    def __init__(
        self,
        *,
        profile_dir: str = SHARED_PROFILE_DIR,
        channel: str | None = "chrome",
        headless: bool = False,
        start_minimized: bool = True,
        launcher: Callable[[], tuple[Any, Callable[[], None]]] | None = None,
    ):
        self.profile_dir = profile_dir
        self.channel = channel
        self.headless = headless
        self.start_minimized = start_minimized
        self._launcher = launcher
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="iris-browser", daemon=True)
        self._thread.start()

    # ── public API ──────────────────────────────────────────────────────────

    def alive(self) -> bool:
        return self._thread.is_alive() and self._error is None

    def submit(self, fn: Callable[[Any], Any], timeout: float | None = None) -> Any:
        """Run ``fn(context)`` on the browser thread and return its result."""
        self._ready.wait()
        if self._error is not None:
            raise self._error
        future: Future = Future()
        self._queue.put((fn, future))
        return future.result(timeout=timeout)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=15)

    # ── worker thread ───────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            if self._launcher is not None:
                context, cleanup = self._launcher()
                try:
                    self._serve(context)
                finally:
                    cleanup()
            else:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as pw:
                    context = self._launch(pw)
                    if self.start_minimized:
                        self._minimize(context)
                    try:
                        self._serve(context)
                    finally:
                        context.close()
        except BaseException as exc:  # noqa: BLE001 - surface to callers via submit()
            self._error = exc
            self._ready.set()

    def _serve(self, context: Any) -> None:
        self._ready.set()
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(context))
            except BaseException as exc:  # noqa: BLE001 - delivered to the caller
                future.set_exception(exc)

    def _launch(self, pw: Any) -> Any:
        os.makedirs(self.profile_dir, exist_ok=True)
        # Prefer real Chrome (passes Cloudflare on chatgpt.com); fall back to Chromium.
        for channel in ([self.channel] if self.channel else []) + [None]:
            try:
                kwargs: dict[str, Any] = {
                    "headless": self.headless,
                    "args": list(_LAUNCH_ARGS),
                }
                if channel:
                    kwargs["channel"] = channel
                return pw.chromium.launch_persistent_context(self.profile_dir, **kwargs)
            except Exception:
                continue
        raise RuntimeError("Não foi possível abrir o navegador compartilhado.")

    def _minimize(self, context: Any) -> None:
        try:
            page = context.pages[0] if context.pages else context.new_page()
            set_window_visible(page, False)
        except Exception:
            pass


# ── module-level singleton ──────────────────────────────────────────────────

_session: BrowserSession | None = None
_lock = threading.Lock()


def get_browser_session(**kwargs: Any) -> BrowserSession:
    """Return the process-wide shared session, (re)creating it if needed."""
    global _session
    with _lock:
        if _session is None or not _session.alive():
            _session = BrowserSession(**kwargs)
        return _session


def close_browser_session() -> None:
    """Close the shared session (e.g. on server shutdown)."""
    global _session
    with _lock:
        if _session is not None:
            try:
                _session.close()
            finally:
                _session = None
