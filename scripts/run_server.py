from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "server:app",
        host=os.environ.get("IRIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("IRIS_PORT", "8501")),
    )


if __name__ == "__main__":
    main()
