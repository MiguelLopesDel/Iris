"""Catalog snapshots — the redesigned backup.

A backup is no longer "a zip of everything". The SQLite catalog (metadata + the
expensive-to-recreate embeddings) is the only thing worth backing up; FAISS is a
regenerable cache and the media library is large and handled separately. This
module produces small, consistent, versioned snapshots of the catalog written
straight to an *external* destination (outside the project's disk quota), plus
media tooling (a by-reference manifest, reconciliation by content hash, and a
deliberate streamed export of the bytes).

Stdlib-only and free of heavy imports (no torch/faiss) so it stays cheap to import
and safe to call from request handlers. FAISS rebuild on restore is injected by the
caller via a ``rebuild_faiss`` callable.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_SUFFIX = ".tar.gz"
_ALLOWED_RESTORE_MEMBERS = {"catalog.db", "best_weights.json", "manifest.json", "media_manifest.json"}


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Consistent SQLite copy ──────────────────────────────────────────────────────


def _consistent_db_copy(db_path: Path, dest_file: Path) -> None:
    """Copy the DB via SQLite's online backup API → consistent even while in use.

    This is the correct fix for the old approach of copying the ``.db`` file while
    a WAL is open (which could capture a torn / inconsistent state).
    """
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest_file))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _db_stats(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*), MAX(embedding_dim), MAX(schema_version) FROM memes"
        ).fetchone()
        count, dim, schema = (row[0] or 0, row[1] or 0, row[2] or 0)
        mrow = conn.execute(
            "SELECT model_name FROM memes WHERE model_name <> '' LIMIT 1"
        ).fetchone()
        model = mrow[0] if mrow else ""
    except sqlite3.OperationalError:
        count, dim, schema, model = 0, 0, 0, ""
    finally:
        conn.close()
    return {"meme_count": count, "embedding_dim": dim, "schema_version": schema, "model_name": model}


def _integrity_ok(db_path: Path) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


# ── Media manifest (by reference — zero bytes) ──────────────────────────────────


def build_media_manifest(db_path: Path) -> list[dict]:
    """Where every indexed file lives + its content hash. Carries no media bytes."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT arquivo, storage_path, source_path, content_hash, file_size FROM memes"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── Snapshot ────────────────────────────────────────────────────────────────────


def catalog_snapshot(
    db_path: Path | str,
    dest_dir: Path | str,
    *,
    weights_path: Path | str | None = None,
    model_name: str = "",
    reason: str = "",
) -> dict:
    """Write a consistent, compressed catalog snapshot into ``dest_dir`` (external).

    Everything is built inside a temp dir *on the destination filesystem*, so the
    project's (quota'd) ``data/`` disk never receives a second copy.
    """
    db_path = Path(db_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_reason = re.sub(r"[^a-z0-9_-]", "", reason.lower())
    name = f"{db_path.stem}__{_now_stamp()}" + (f"__{safe_reason}" if safe_reason else "") + SNAPSHOT_SUFFIX
    out_path = dest_dir / name

    with tempfile.TemporaryDirectory(dir=str(dest_dir)) as td:
        tdp = Path(td)
        db_copy = tdp / "catalog.db"
        _consistent_db_copy(db_path, db_copy)

        stats = _db_stats(db_copy)
        media_manifest = build_media_manifest(db_copy)
        (tdp / "media_manifest.json").write_text(
            json.dumps(media_manifest, ensure_ascii=False), encoding="utf-8"
        )
        manifest = {
            "version": "2.0",
            "software": "iris",
            "kind": "catalog",
            "created_at": _iso_now(),
            "reason": reason,
            "db_name": db_path.name,
            "schema_version": stats["schema_version"],
            "meme_count": stats["meme_count"],
            "embedding_dim": stats["embedding_dim"],
            "model_name": model_name or stats["model_name"],
            "media_count": len(media_manifest),
        }
        (tdp / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        members = [
            (db_copy, "catalog.db"),
            (tdp / "media_manifest.json", "media_manifest.json"),
            (tdp / "manifest.json", "manifest.json"),
        ]
        if weights_path and Path(weights_path).exists():
            members.append((Path(weights_path), "best_weights.json"))

        tmp_archive = tdp / ("_building" + SNAPSHOT_SUFFIX)
        with tarfile.open(tmp_archive, "w:gz", compresslevel=6) as tar:
            for src, arc in members:
                tar.add(src, arcname=arc)
        os.replace(tmp_archive, out_path)

    manifest["id"] = name
    manifest["path"] = str(out_path)
    manifest["size_bytes"] = out_path.stat().st_size
    return manifest


def list_snapshots(dest_dir: Path | str) -> list[dict]:
    """List snapshots in ``dest_dir`` (newest first), each described by its manifest."""
    dest_dir = Path(dest_dir)
    out: list[dict] = []
    if not dest_dir.exists():
        return out
    for path in dest_dir.glob("*" + SNAPSHOT_SUFFIX):
        info: dict = {
            "id": path.name, "path": str(path), "size_bytes": path.stat().st_size,
            "created_at": "", "reason": "", "meme_count": None, "db_name": "",
        }
        try:
            with tarfile.open(path, "r:gz") as tar:
                member = tar.extractfile("manifest.json")
                if member is not None:
                    info.update(json.loads(member.read().decode("utf-8")))
            info["id"] = path.name
            info["path"] = str(path)
            info["size_bytes"] = path.stat().st_size
        except (tarfile.TarError, OSError, KeyError, ValueError):
            info["corrupt"] = True
        out.append(info)
    out.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return out


def apply_retention(dest_dir: Path | str, keep_last: int) -> list[str]:
    """Delete all but the ``keep_last`` most-recent snapshots. Returns removed ids."""
    if not keep_last or keep_last < 1:
        return []
    removed: list[str] = []
    for snap in list_snapshots(dest_dir)[keep_last:]:
        try:
            Path(snap["path"]).unlink()
            removed.append(snap["id"])
        except OSError:
            pass
    return removed


# ── Restore ─────────────────────────────────────────────────────────────────────


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    for member in tar.getmembers():
        if member.isfile() and member.name in _ALLOWED_RESTORE_MEMBERS:
            tar.extract(member, path=dest)


def read_snapshot_manifest(snapshot_path: Path | str) -> dict:
    with tarfile.open(snapshot_path, "r:gz") as tar:
        member = tar.extractfile("manifest.json")
        if member is None:
            raise ValueError("Snapshot sem manifest.json")
        return json.loads(member.read().decode("utf-8"))


def restore_snapshot(
    snapshot_path: Path | str,
    *,
    db_path: Path | str,
    weights_path: Path | str | None = None,
    mode: str = "overlay",
    rebuild_faiss: Callable[[Path, str], None] | None = None,
    model_name: str = "",
) -> dict:
    """Restore a catalog snapshot over ``db_path`` and rebuild FAISS.

    Extracts the embedded ``catalog.db`` into a temp on the *same* filesystem as
    ``db_path``, verifies ``integrity_check``, then atomically swaps it in. Caller
    is responsible for taking a pre-restore snapshot first (reversibility).
    """
    snapshot_path = Path(snapshot_path)
    db_path = Path(db_path)
    data_dir = db_path.parent
    data_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=str(data_dir)) as td:
        tdp = Path(td)
        with tarfile.open(snapshot_path, "r:gz") as tar:
            _safe_extract(tar, tdp)
        restored_db = tdp / "catalog.db"
        if not restored_db.exists():
            raise ValueError("Snapshot inválido: catalog.db ausente")
        if not _integrity_ok(restored_db):
            raise ValueError("Snapshot corrompido: integrity_check falhou")

        os.replace(restored_db, db_path)  # same fs (data_dir) → atomic
        # Drop stale WAL/SHM of the previous DB so nothing is mixed in.
        for sfx in ("-wal", "-shm"):
            Path(str(db_path) + sfx).unlink(missing_ok=True)

        restored_weights = tdp / "best_weights.json"
        if restored_weights.exists() and weights_path:
            shutil.copy2(restored_weights, weights_path)

    result: dict = {"db_path": str(db_path), "mode": mode}
    if mode == "mirror":
        result["removed"] = _mirror_cleanup(data_dir, db_path)
    if rebuild_faiss is not None:
        rebuild_faiss(db_path, model_name)
        result["faiss_rebuilt"] = True
    return result


def _mirror_cleanup(data_dir: Path, keep_db: Path) -> list[str]:
    """Remove other DBs / FAISS / manifests so the dir mirrors the restored snapshot."""
    removed: list[str] = []
    keep_db = keep_db.resolve()
    keep_stem = keep_db.stem
    candidates = (
        list(data_dir.glob("*.db"))
        + list(data_dir.glob("*.faiss"))
        + list(data_dir.glob("*_manifest.json"))
    )
    for path in candidates:
        if path.resolve() == keep_db or path.name.startswith(keep_stem):
            continue  # the restored DB and its (to-be-rebuilt) indices stay
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            pass
    return removed


# ── Media: reconcile (relink by hash) + deliberate export ───────────────────────


def _library_path(library_root: Path, item: dict) -> Path | None:
    sp = item.get("storage_path")
    return library_root / sp if sp else None


def _sha256(path: Path, chunk: int = 1 << 16) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def reconcile_media(
    db_path: Path | str, library_root: Path | str, originals_root: Path | str
) -> dict:
    """Verify library files; relink missing ones from the originals tree by hash.

    Only same-size originals are hashed (cheap pre-filter), so this scales to large
    collections. Files not managed by the library (no ``storage_path``) are ignored.
    """
    library_root = Path(library_root)
    originals_root = Path(originals_root)
    manifest = build_media_manifest(db_path)

    managed = [it for it in manifest if it.get("storage_path")]
    missing = [it for it in managed if not (_library_path(library_root, it) or Path()).exists()]

    relinked: list[str] = []
    still_missing: list[str] = []
    if missing:
        size_index: dict[int, list[Path]] = {}
        if originals_root.exists():
            for p in originals_root.rglob("*"):
                if p.is_file():
                    size_index.setdefault(p.stat().st_size, []).append(p)

        hash_cache: dict[Path, str] = {}
        for item in missing:
            target_hash = item.get("content_hash")
            size = item.get("file_size") or 0
            src = None
            for cand in size_index.get(int(size), []):
                if cand not in hash_cache:
                    try:
                        hash_cache[cand] = _sha256(cand)
                    except OSError:
                        hash_cache[cand] = ""
                if target_hash and hash_cache[cand] == target_hash:
                    src = cand
                    break
            dest = _library_path(library_root, item)
            if src is not None and dest is not None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                relinked.append(item.get("arquivo", dest.name))
            else:
                still_missing.append(item.get("arquivo", item.get("storage_path", "?")))

    return {
        "total": len(managed),
        "present": len(managed) - len(missing),
        "relinked": relinked,
        "missing": still_missing,
    }


def export_media(library_root: Path | str, dest_dir: Path | str, *, name: str | None = None) -> dict:
    """Stream the library into an uncompressed tar on ``dest_dir`` (external).

    Deliberate, heavy, and separate from routine snapshots. Media is already
    compressed, so no gzip. Written directly to the destination — no temp on the
    project's quota'd disk.
    """
    library_root = Path(library_root)
    dest_dir = Path(dest_dir)
    if not library_root.exists():
        raise ValueError(f"Biblioteca não encontrada: {library_root}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = name or f"iris_library__{_now_stamp()}.tar"
    out_path = dest_dir / name
    tmp_path = dest_dir / ("_building_" + name)

    count = 0
    total = 0
    try:
        with tarfile.open(tmp_path, "w") as tar:
            for path in sorted(library_root.rglob("*")):
                if path.is_file():
                    tar.add(path, arcname=path.relative_to(library_root).as_posix())
                    count += 1
                    total += path.stat().st_size
        os.replace(tmp_path, out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    return {
        "path": str(out_path),
        "files": count,
        "bytes": total,
        "size_bytes": out_path.stat().st_size,
    }
