"""The disposable performance probe must keep working as the API evolves."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_stage0_probe_exercises_gallery_search_thumbnail_and_sync() -> None:
    root = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from scripts.measure_stage0 import measure; "
            "print('STAGE0=' + json.dumps(measure(4, 2)))",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    marker = next(line for line in process.stdout.splitlines() if line.startswith("STAGE0="))
    result = json.loads(marker.removeprefix("STAGE0="))
    assert result["synthetic_library"]["items"] == 4
    assert result["first_page_cold_ms"] >= 0
    assert result["thumbnail_cold_ms"] >= 0
    assert result["filename_search"]["median_ms"] >= 0
    assert result["sync_upload_start_chunk_complete"]["median_ms"] >= 0
