"""The background thumbnail warm-up: newest first, only what is missing."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.thumbnail_warmer import Library, ThumbnailWarmer, missing_thumbnails


@dataclass
class Record:
    resolved_path: str | None
    file_mtime: float | None


def _key(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()


def _generate(made: list[str]):
    def generate(source: str, target: Path) -> None:
        made.append(Path(source).name)
        target.write_bytes(b"jpeg")
    return generate


def test_missing_thumbnails_are_built_newest_first_and_only_once(tmp_path: Path) -> None:
    thumbs = tmp_path / "thumbnails"
    records = [
        Record("/library/old.jpg", 100.0),
        Record("/library/new.jpg", 300.0),
        Record("/library/middle.jpg", 200.0),
        Record("/library/new.jpg", 300.0),  # the same file twice in the catalogue
        Record(None, 400.0),                # no original to read
        Record("/library/undated.jpg", float("nan")),
    ]
    made: list[str] = []
    warmer = ThumbnailWarmer(lambda: [Library("ana", thumbs, lambda: records)], _key, _generate(made), pause=0)

    assert warmer.run_once() == 4
    assert made == ["new.jpg", "middle.jpg", "old.jpg", "undated.jpg"]


def test_existing_thumbnails_are_left_alone(tmp_path: Path) -> None:
    thumbs = tmp_path / "thumbnails"
    thumbs.mkdir()
    (thumbs / f"{_key('/library/a.jpg')}.jpg").write_bytes(b"already there")
    records = [Record("/library/a.jpg", 1.0), Record("/library/b.jpg", 2.0)]

    assert [source for source, _ in missing_thumbnails(records, thumbs, _key)] == ["/library/b.jpg"]


def test_one_unreadable_original_does_not_stop_the_rest(tmp_path: Path) -> None:
    made: list[str] = []
    good = _generate(made)

    def flaky(source: str, target: Path) -> None:
        if source.endswith("broken.jpg"):
            raise OSError("cannot decode")
        good(source, target)

    records = [Record("/library/broken.jpg", 2.0), Record("/library/fine.jpg", 1.0)]
    warmer = ThumbnailWarmer(lambda: [Library("ana", tmp_path / "t", lambda: records)], _key, flaky, pause=0)

    assert warmer.run_once() == 1
    assert made == ["fine.jpg"]


def test_stopping_ends_the_run(tmp_path: Path) -> None:
    made: list[str] = []
    warmer: ThumbnailWarmer

    def generate_then_stop(source: str, target: Path) -> None:
        _generate(made)(source, target)
        warmer.stop(timeout=0)

    records = [Record(f"/library/{n}.jpg", float(n)) for n in range(5)]
    warmer = ThumbnailWarmer(
        lambda: [Library("ana", tmp_path / "t", lambda: records)], _key, generate_then_stop, pause=0
    )

    assert warmer.run_once() == 1
