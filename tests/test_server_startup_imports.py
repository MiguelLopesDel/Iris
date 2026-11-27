"""What a model-free server loads at start.

Importing torch took most of a server start (well over ten seconds on a
spinning disk), so every restart and every hot reload paid for it even with
IRIS_LOAD_MODEL=0, which never runs a tensor. The import runs in a fresh
interpreter: the test process itself has torch loaded by other tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HEAVY = ("torch", "transformers", "sentence_transformers", "whisper", "easyocr", "cv2")


def test_model_free_server_starts_without_ml_libraries(tmp_path):
    env = {**os.environ, "IRIS_LOAD_MODEL": "0", "PYTHONPATH": str(ROOT)}
    probe = (
        "import sys, server; "
        f"print(' '.join(name for name in {HEAVY!r} if name in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"loaded at import: {result.stdout.strip()}"
