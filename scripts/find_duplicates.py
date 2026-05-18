from __future__ import annotations

import argparse
import json
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core.duplicates import find_duplicate_groups  # noqa: E402
from core.search_engine import MemeSearchEngine  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Lista imagens duplicadas ou quase duplicadas.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--media-root", default="media")
    parser.add_argument("--threshold", type=float, default=0.985)
    parser.add_argument("--max-neighbors", type=int, default=12)
    parser.add_argument("--output", default="data/reports/duplicates.json")
    args = parser.parse_args()

    engine = MemeSearchEngine(db_path=args.db, media_root=args.media_root, load_model=False)
    groups = find_duplicate_groups(
        engine,
        threshold=args.threshold,
        max_neighbors=args.max_neighbors,
    )
    payload = {
        "db": args.db,
        "threshold": args.threshold,
        "group_count": len(groups),
        "item_count": sum(len(group.items) for group in groups),
        "groups": [
            {
                "group_id": group.group_id,
                "score": group.score,
                "items": [item.__dict__ for item in group.items],
            }
            for group in groups
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ["group_count", "item_count"]}, indent=2))
    print(f"Relatorio salvo em: {output}")


if __name__ == "__main__":
    main()
