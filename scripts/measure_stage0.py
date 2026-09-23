"""Measure model-free Iris API latency against a disposable synthetic library.

Run with ``python -m scripts.measure_stage0 --items 1000 --rounds 10``.
No personal library or running server is accessed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from core.auth import hash_password
from core.indexer_db import init_db
from core.users_db import create_user


def _milliseconds(action: Callable[[], Any]) -> tuple[float, Any]:
    before = time.perf_counter()
    result = action()
    return (time.perf_counter() - before) * 1000, result


def _summarize(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 2),
        "max_ms": round(ordered[-1], 2),
    }


def measure(items: int, rounds: int) -> dict:
    if items < 1 or rounds < 1:
        raise ValueError("items and rounds must be positive")

    # Server module startup binds to the current directory and environment.
    # Keep it isolated, and make a second invocation in the same process explicit.
    if "server" in sys.modules:
        raise RuntimeError("measure_stage0 must run in a fresh process")

    project_root = str(Path(__file__).resolve().parents[1])
    sys.path.insert(0, project_root)
    previous_cwd = Path.cwd()
    previous_env = {
        name: os.environ.get(name)
        for name in ("IRIS_LOAD_MODEL", "IRIS_SERVER_MODE", "IRIS_SESSION_HTTPS_ONLY")
    }
    with tempfile.TemporaryDirectory(prefix="iris-stage0-") as temporary:
        try:
            os.chdir(temporary)
            os.environ.update(
                IRIS_LOAD_MODEL="0",
                IRIS_SERVER_MODE="private",
                IRIS_SESSION_HTTPS_ONLY="false",
            )

            data = Path("data")
            user = create_user(
                data / "users.db",
                data,
                username="stage0",
                password_hash=hash_password("synthetic measurement password"),
                is_admin=True,
            )
            buffer = io.BytesIO()
            Image.new("RGB", (32, 32), (80, 120, 180)).save(buffer, format="JPEG")
            image_bytes = buffer.getvalue()
            rows = []
            for number in range(items):
                path = user.media_root / f"sample-{number:06d}.jpg"
                path.write_bytes(image_bytes)
                rows.append((path.name, str(path.resolve()), b"\0" * 16))
            init_db(user.db_path).close()
            with sqlite3.connect(user.db_path) as connection:
                connection.executemany(
                    "INSERT INTO memes (arquivo, caminho, embedding) VALUES (?, ?, ?)", rows
                )

            from fastapi.testclient import TestClient

            import server

            with TestClient(server.app) as web, TestClient(server.app) as device:
                login = web.post(
                    "/api/auth/login",
                    data={
                        "username": "stage0",
                        "password": "synthetic measurement password",
                    },
                )
                assert login.status_code == 200, login.text

                first_page_ms, first_page = _milliseconds(
                    lambda: web.get("/api/records", params={"page": 1, "per_page": 50})
                )
                assert first_page.status_code == 200, first_page.text
                assert first_page.json()["total"] == items
                thumbnail = first_page.json()["records"][0]["thumbnail_url"]
                thumbnail_cold_ms, first_thumbnail = _milliseconds(
                    lambda: web.get(thumbnail)
                )
                assert first_thumbnail.status_code == 200, first_thumbnail.text

                pages = []
                searches = []
                thumbnails = []
                for _ in range(rounds):
                    elapsed, page = _milliseconds(
                        lambda: web.get("/api/records", params={"page": 1, "per_page": 50})
                    )
                    assert page.status_code == 200, page.text
                    pages.append(elapsed)

                    elapsed, search = _milliseconds(
                        lambda: web.get("/api/search/filename", params={"q": "sample", "top_k": 50})
                    )
                    assert search.status_code == 200, search.text
                    searches.append(elapsed)

                    elapsed, image = _milliseconds(lambda: web.get(thumbnail))
                    assert image.status_code == 200, image.text
                    thumbnails.append(elapsed)

                device_login = device.post(
                    "/api/auth/devices/login",
                    data={
                        "username": "stage0",
                        "password": "synthetic measurement password",
                        "device_name": "Synthetic phone",
                        "platform": "android",
                    },
                )
                assert device_login.status_code == 200, device_login.text
                headers = {
                    "Authorization": "Bearer " + device_login.json()["access_token"]
                }
                uploads = []
                for number in range(rounds):
                    filename = f"upload-{number:06d}.jpg"

                    def upload(filename: str = filename) -> None:
                        started = device.post(
                            "/api/sync/uploads",
                            headers=headers,
                            json={
                                "filename": filename,
                                "size": len(image_bytes),
                                "sha256": hashlib.sha256(image_bytes).hexdigest(),
                            },
                        )
                        assert started.status_code == 200, started.text
                        url = "/api/sync/uploads/" + started.json()["upload_id"]
                        chunk = device.put(url + "?offset=0", headers=headers, content=image_bytes)
                        assert chunk.status_code == 200, chunk.text
                        completed = device.post(url + "/complete", headers=headers)
                        assert completed.status_code == 200, completed.text

                    elapsed, _ = _milliseconds(upload)
                    uploads.append(elapsed)

            return {
                "environment": {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "cpu_count": os.cpu_count(),
                    "ram_total_gib": round(
                        os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3,
                        1,
                    ),
                    "model_loaded": False,
                    "client": "in-process TestClient; excludes network and Android rendering",
                },
                "synthetic_library": {"accounts": 1, "items": items, "rounds": rounds},
                "first_page_cold_ms": round(first_page_ms, 2),
                "thumbnail_cold_ms": round(thumbnail_cold_ms, 2),
                "gallery_page_warm": _summarize(pages),
                "filename_search": _summarize(searches),
                "thumbnail_request": _summarize(thumbnails),
                "sync_upload_start_chunk_complete": _summarize(uploads),
            }
        finally:
            os.chdir(previous_cwd)
            sys.path.remove(project_root)
            for name, value in previous_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(measure(args.items, args.rounds), indent=2))


if __name__ == "__main__":
    main()
