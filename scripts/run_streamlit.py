from __future__ import annotations

import subprocess
import sys


def main() -> int:
    return subprocess.call([sys.executable, "-m", "streamlit", "run", "app/main.py"])


if __name__ == "__main__":
    raise SystemExit(main())
