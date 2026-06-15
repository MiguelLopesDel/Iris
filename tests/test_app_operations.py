from __future__ import annotations

import io
import zipfile

from core.app_operations import (
    backup_inventory,
    create_backup_zip,
    inspect_backup_zip,
    restore_backup_zip,
)


def test_backup_round_trip(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "iris.db").write_bytes(b"database")
    (source / "iris_image.faiss").write_bytes(b"index")
    (source / "best_weights.json").write_text('{"balance": 0.5}', encoding="utf-8")
    library = source / "library" / "default"
    library.mkdir(parents=True)
    (library / "photo.jpg").write_bytes(b"image")

    payload = create_backup_zip(source, include_library=True)
    summary = inspect_backup_zip(payload)

    assert summary["databases"] == 2
    assert summary["config"] == 1
    assert summary["library"] == 1

    target = tmp_path / "target"
    counts = restore_backup_zip(payload, target)

    assert counts == {"databases": 2, "config": 1, "library": 1}
    assert (target / "iris.db").read_bytes() == b"database"
    assert (target / "library" / "default" / "photo.jpg").read_bytes() == b"image"


def test_restore_ignores_path_traversal(tmp_path):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", '{"library_included": true}')
        archive.writestr("databases/../../escape.db", b"bad")
        archive.writestr("databases/good.db", b"good")

    counts = restore_backup_zip(output.getvalue(), tmp_path / "data")

    assert counts["databases"] == 1
    assert not (tmp_path / "escape.db").exists()
    assert (tmp_path / "data" / "good.db").read_bytes() == b"good"


def test_backup_inventory_counts_files(tmp_path):
    (tmp_path / "one.db").write_bytes(b"db")
    (tmp_path / "one_image.faiss").write_bytes(b"faiss")
    library = tmp_path / "library"
    library.mkdir()
    (library / "one.jpg").write_bytes(b"image")

    inventory = backup_inventory(tmp_path)

    assert inventory["databases"] == 1
    assert inventory["indexes"] == 1
    assert inventory["library_files"] == 1
