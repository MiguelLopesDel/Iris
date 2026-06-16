"""Static wiring guards for the web UI.

Regression net for the bug class where JS references a DOM element that no longer
exists (``getElementById('x').addEventListener`` on ``null`` throws at module
load and silently kills every listener registered afterwards -- "clicking does
nothing"), and for broken static asset references.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "templates" / "index.html"
APP_JS = ROOT / "static" / "app.js"


def _html_ids() -> set[str]:
    return set(re.findall(r'id="([^"]+)"', INDEX.read_text(encoding="utf-8")))


def test_addeventlistener_targets_exist_in_html() -> None:
    """Every ``getElementById('x').addEventListener`` must have a matching id in
    the HTML -- otherwise the module throws on load and the whole UI breaks."""
    app = APP_JS.read_text(encoding="utf-8")
    targets = re.findall(r"""getElementById\(['"]([^'"]+)['"]\)\.addEventListener""", app)
    ids = _html_ids()

    missing = sorted(t for t in targets if t not in ids)
    assert not missing, f"app.js liga listeners a ids inexistentes no HTML: {missing}"


def test_referenced_static_assets_exist() -> None:
    """Every /static asset referenced in index.html must exist on disk."""
    html = INDEX.read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="(/static/[^"?]+)', html)
    missing = [r for r in refs if not (ROOT / r.lstrip("/")).is_file()]
    assert not missing, f"index.html referencia assets inexistentes: {missing}"


def test_local_scripts_are_cache_busted() -> None:
    """Local JS/CSS includes carry a ?v= query so the browser fetches new
    versions (a stale cache served an old app.js and broke the UI)."""
    html = INDEX.read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="(/static/[^"]+\.(?:js|css)[^"]*)"', html)
    unversioned = [r for r in refs if "?v=" not in r]
    assert not unversioned, f"assets locais sem cache-busting ?v=: {unversioned}"
