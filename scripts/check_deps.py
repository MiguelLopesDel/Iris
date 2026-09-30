"""Check an installed dependency lock, tolerating only the deliberate exclusions.

The locks leave out opencv-python and, in the CUDA profile, onnxruntime: they
install the same modules as opencv-python-headless and onnxruntime-gpu (see
scripts/lock_deps.sh). `pip check` reports those as missing; any other problem
it reports still fails this check.

    python scripts/check_deps.py            # CPU profile
    python scripts/check_deps.py --cuda     # NVIDIA profile, also requires CUDA providers
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from importlib import metadata

EXCLUDED = {"opencv-python", "onnxruntime"}
_MISSING = re.compile(r"^(\S+) \S+ requires (\S+), which is not installed\.$")


def unexpected_problems(pip_check_output: str) -> list[str]:
    problems = []
    for line in pip_check_output.splitlines():
        line = line.strip()
        if not line or line == "No broken requirements found.":
            continue
        match = _MISSING.match(line)
        if match and match.group(2).lower() in EXCLUDED:
            continue
        problems.append(line)
    return problems


def _installed(name: str) -> bool:
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cuda", action="store_true", help="validate the NVIDIA profile")
    args = parser.parse_args()

    result = subprocess.run(
        [sys.executable, "-m", "pip", "check"], capture_output=True, text=True, check=False
    )
    problems = unexpected_problems(result.stdout + result.stderr)

    if _installed("opencv-python"):
        problems.append("opencv-python is installed; it overwrites opencv-python-headless")
    runtime = "onnxruntime-gpu" if args.cuda else "onnxruntime"
    other = "onnxruntime" if args.cuda else "onnxruntime-gpu"
    if not _installed(runtime):
        problems.append(f"{runtime} is not installed")
    if _installed(other):
        problems.append(f"{other} is installed next to {runtime}; both provide `onnxruntime`")

    import cv2  # noqa: F401
    import faiss  # noqa: F401
    import insightface  # noqa: F401
    import onnxruntime
    import torch
    import torchvision  # noqa: F401

    if args.cuda:
        if torch.version.cuda is None:
            problems.append(f"torch {torch.__version__} is not a CUDA build")
        if "CUDAExecutionProvider" not in onnxruntime.get_available_providers():
            problems.append("onnxruntime has no CUDAExecutionProvider")
    elif torch.version.cuda is not None:
        problems.append(f"torch {torch.__version__} is a CUDA build in the CPU profile")

    for problem in problems:
        print(f"dependency check: {problem}", file=sys.stderr)
    if not problems:
        print(f"dependencies ok: torch {torch.__version__}, onnxruntime {onnxruntime.__version__}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
