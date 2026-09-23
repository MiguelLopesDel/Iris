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


def test_every_module_binds_listeners_to_existing_ids() -> None:
    """Same guard as above, for every module loaded at startup: a missing id in
    spaces.js or system.js breaks its whole tab just as surely."""
    ids = _html_ids()
    missing: dict[str, list[str]] = {}
    for module in sorted((ROOT / "static").glob("*.js")):
        source = module.read_text(encoding="utf-8")
        targets = re.findall(
            r"""(?:getElementById|\$)\(['"]([^'"]+)['"]\)\.addEventListener""", source
        )
        absent = sorted({target for target in targets if target not in ids})
        if absent:
            missing[module.name] = absent
    assert not missing, f"listeners ligados a ids inexistentes no HTML: {missing}"


def test_module_imports_agree_on_cache_versions() -> None:
    """A module imported as ./x.js?v=1 in one place and ?v=2 in another is two
    modules to the browser: one of them is stale, with its own copy of state."""
    versions: dict[str, set[str]] = {}
    for module in (ROOT / "static").glob("*.js"):
        for name, version in re.findall(
            r"""from\s+['"]\./([\w-]+\.js)\?v=(\d+)['"]""", module.read_text(encoding="utf-8")
        ):
            versions.setdefault(name, set()).add(version)
    disagreeing = {name: sorted(found) for name, found in versions.items() if len(found) > 1}
    assert not disagreeing, f"módulos importados com versões diferentes: {disagreeing}"


def test_shared_spaces_only_show_up_with_accounts() -> None:
    """Spaces exist between accounts; a single-user install must not offer them."""
    html = INDEX.read_text(encoding="utf-8")
    entries = re.findall(r"<[^>]*data-(?:primary|go)-tab=\"spaces\"[^>]*>", html)
    assert len(entries) >= 3  # desktop tab, mobile tab, Organizar card
    for entry in entries:
        assert "data-requires-accounts" in entry and " hidden" in entry, entry
    assert re.search(r'id="btn-space-selected"[^>]*data-requires-accounts[^>]*hidden', html)
