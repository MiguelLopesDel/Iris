from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which


def to_file_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def move_to_trash(paths: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    send2trash_fn = None
    try:
        from send2trash import send2trash
        send2trash_fn = send2trash
    except Exception:
        send2trash_fn = None

    moved: list[str] = []
    failed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = str(Path(raw_path).resolve())
        if path in seen:
            continue
        seen.add(path)
        file_path = Path(path)
        if not file_path.exists():
            failed.append((path, "arquivo nao encontrado"))
            continue
        try:
            if send2trash_fn is not None:
                send2trash_fn(path)
            else:
                fallback_trash(path)
            moved.append(path)
        except Exception as exc:
            failed.append((path, str(exc)))
    return moved, failed


def fallback_trash(path: str) -> None:
    gio_bin = which("gio")
    if gio_bin:
        result = subprocess.run(
            [gio_bin, "trash", path],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or "falha ao mover para lixeira via gio")

    raise RuntimeError("send2trash nao instalado e 'gio trash' indisponivel")
