"""python -m scripts.perf_lab run SCENARIO --root EMPTY_DIR [--output DIR]"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.perf_lab import scenario as scenario_mod


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.perf_lab", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser(
        "run", help="run a scenario and write report.md and report.json"
    )
    run_parser.add_argument("scenario", type=Path)
    run_parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="a new, empty directory for the disposable server's data (put it on the disk to test)",
    )
    run_parser.add_argument(
        "--output", type=Path, help="where to write the report (default: ROOT/report)"
    )
    run_parser.add_argument(
        "--variant",
        default="",
        help="run a middleware variant of the app instead of the real stack "
        "(see scripts/perf_lab/variants.py): no_gzip, trivial_auth_http, trivial_auth_asgi, ...",
    )
    check_parser = commands.add_parser("check", help="validate a scenario file")
    check_parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    scenario = scenario_mod.load(args.scenario)
    if args.command == "check":
        print(
            f"{scenario.name}: {len(scenario.actors)} actor(s), hard cap {scenario.duration:.0f} s — valid"
        )
        return 0
    from scripts.perf_lab.runner import run

    result = run(
        scenario, args.root.resolve(), (args.output or args.root / "report").resolve(), args.variant
    )
    for name, actor in result["actors"].items():
        s = actor["summary"]
        print(
            f"[perf-lab] {name}: {s['items_per_s']} items/s, {s['mb_per_s']} MB/s, "
            f"{s['items_done']} done, {s['items_failed']} failed"
        )
    for flag in result["possible_bottlenecks"]:
        print(
            f"[perf-lab] possible bottleneck: {flag['resource']} {flag['from_s']}–{flag['to_s']} s"
        )
    print(f"[perf-lab] report: {(args.output or args.root / 'report').resolve() / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
