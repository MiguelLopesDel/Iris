"""Persistent app settings (data/iris_settings.json).

The app historically had no on-disk config — runtime config came only from env
vars seeded into ``server._active_config``. The backup redesign needs a couple of
persisted preferences (notably the external backup destination), so this module
provides a tiny, stdlib-only load/save layer plus destination validation.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

CONFIG_PATH = Path("data/iris_settings.json")

DEFAULTS: dict[str, object] = {
    "backup_dir": "",            # external destination for catalog snapshots (empty = unset)
    "backup_auto": True,         # take a snapshot before risky ops (import / trash / restore)
    "backup_keep_last": 10,      # retention: keep this many most-recent snapshots
    "media_originals_root": "media",  # where to relink missing library files from, by hash
}

_ALLOWED = set(DEFAULTS)


def load(path: Path | None = None) -> dict:
    """Return settings merged over defaults. Missing/corrupt file → defaults."""
    path = path or CONFIG_PATH  # resolved at call time so tests can relocate it
    data = dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for key in _ALLOWED:
                if key in raw:
                    data[key] = raw[key]
    except (OSError, ValueError):
        pass
    return data


def save(updates: dict, path: Path | None = None) -> dict:
    """Merge ``updates`` into the stored settings and write atomically."""
    path = path or CONFIG_PATH
    current = load(path)
    for key, value in updates.items():
        if key in _ALLOWED:
            current[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".iris-settings-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return current


def validate_backup_dir(backup_dir: str, data_dir: Path = Path("data")) -> dict:
    """Check a candidate destination. Returns {ok, error?, warnings[], resolved}.

    Best-effort: tries to create the directory and confirm it's writable. Warns when
    it lives on the same device as ``data/`` (defeats the anti-quota / disk-failure
    purpose), but does not block — the user may knowingly want a local folder.
    """
    result: dict = {"ok": False, "warnings": [], "resolved": ""}
    raw = (backup_dir or "").strip()
    if not raw:
        result["error"] = "Destino de backup não configurado."
        return result
    target = Path(raw).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
        resolved = target.resolve()
    except OSError as exc:
        result["error"] = f"Não foi possível criar/usar o destino: {exc}"
        return result
    if not os.access(resolved, os.W_OK):
        result["error"] = "Destino existe mas não é gravável."
        return result
    result["resolved"] = str(resolved)
    try:
        if resolved.stat().st_dev == data_dir.resolve().stat().st_dev:
            result["warnings"].append(
                "O destino está no mesmo dispositivo que 'data/' — não protege contra "
                "falha do disco nem contra a cota se ela for global. Prefira um disco externo."
            )
    except OSError:
        pass
    result["ok"] = True
    return result
