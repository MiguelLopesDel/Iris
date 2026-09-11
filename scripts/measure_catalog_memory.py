#!/usr/bin/env python3
"""What one more item costs the process, measured as a slope.

Memory per item is not RSS divided by item count: a process that imports torch
carries a gigabyte before the first record exists, and dividing that by N gives
a number that shrinks as the catalogue grows while nothing about the catalogue
changed. The question is the derivative -- how much RSS the next item adds --
so this loads several catalogue sizes and fits a line.

Each size runs in its **own process**. Freeing a structure does not return the
arena to the operating system, so two engines measured in one process report the
peak of the first for both.

    python scripts/measure_catalog_memory.py --db data/meme_compass_full_v1.db
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _rss_kb() -> int:
    with open("/proc/self/status") as handle:
        for line in handle:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


def _child(db_path: str) -> int:
    """Load one catalogue and report RSS before and after. Runs alone."""
    from core.search_engine import IrisEngine

    baseline = _rss_kb()
    engine = IrisEngine(db_path=db_path, load_model=False)
    count = len(engine.records)
    print(json.dumps({"count": count, "baseline_kb": baseline, "loaded_kb": _rss_kb()}))
    return 0


def _subset(source: Path, limit: int, destination: Path) -> int:
    """A catalogue of the first ``limit`` rows, everything else preserved."""
    shutil.copy(source, destination)
    conn = sqlite3.connect(destination)
    try:
        conn.execute(
            "DELETE FROM memes WHERE id NOT IN (SELECT id FROM memes ORDER BY id LIMIT ?)",
            (limit,),
        )
        conn.commit()
        conn.execute("VACUUM")
        return int(conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0])
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/meme_compass_full_v1.db"))
    parser.add_argument("--sizes", type=int, nargs="+", default=[2000, 6000, 11000, 17000])
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--child", type=str, default=None)
    args = parser.parse_args()

    if args.child:
        return _child(args.child)

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="iris-slope-"))
    workdir.mkdir(parents=True, exist_ok=True)

    points: list[tuple[int, float]] = []
    print(f"{'itens':>8} {'base MB':>9} {'carregado MB':>13} {'delta MB':>9} {'KB/item':>9}")
    print("-" * 52)
    for size in args.sizes:
        subset = workdir / f"subset_{size}.db"
        if not subset.exists():
            _subset(args.db, size, subset)
        # A fresh interpreter per size: the allocator does not give arenas back,
        # so a second engine in the same process measures the first one's peak.
        result = subprocess.run(
            [sys.executable, __file__, "--child", str(subset)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        )
        if result.returncode != 0:
            print(result.stderr.strip()[-2000:], file=sys.stderr)
            return 1
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        count = payload["count"]
        delta = (payload["loaded_kb"] - payload["baseline_kb"]) / 1024
        points.append((count, payload["loaded_kb"] - payload["baseline_kb"]))
        print(
            f"{count:>8,} {payload['baseline_kb'] / 1024:>9.0f}"
            f" {payload['loaded_kb'] / 1024:>13.0f} {delta:>9.0f}"
            f" {(payload['loaded_kb'] - payload['baseline_kb']) / max(count, 1):>9.2f}",
            flush=True,
        )

    if len(points) >= 2:
        # Least squares over the points, because the intercept is the process
        # itself and only the slope belongs to the catalogue.
        n = len(points)
        sum_x = sum(x for x, _ in points)
        sum_y = sum(y for _, y in points)
        sum_xy = sum(x * y for x, y in points)
        sum_xx = sum(x * x for x, _ in points)
        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_xx - sum_x * sum_x)
        intercept = (sum_y - slope * sum_x) / n
        print()
        print(f"inclinação: {slope:.2f} KB/item   intercepto: {intercept / 1024:.0f} MB")
        budget = 8 * 1024 * 1024  # 8 GB machine, in KB
        print(f"teto em 8 GB (descontado o intercepto): {(budget - intercept) / slope:,.0f} itens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
