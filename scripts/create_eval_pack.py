from __future__ import annotations

import argparse
import json
from pathlib import Path

from _path import ensure_project_root
from PIL import Image, ImageDraw

ensure_project_root()

from core.evaluation import write_eval_template  # noqa: E402
from core.media_inventory import read_manifest  # noqa: E402


def make_thumbnail(path: Path, size: int) -> Image.Image:
    try:
        image = Image.open(path).convert("RGB")
    except Exception:
        image = Image.new("RGB", (size, size), "white")
        ImageDraw.Draw(image).text((8, 8), "preview unavailable", fill="black")
    image.thumbnail((size, size))
    canvas = Image.new("RGB", (size, size), "white")
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera thumbnails, contact sheet e template de consultas esperadas."
    )
    parser.add_argument("--manifest", default="data/eval/samples/sample_100.json")
    parser.add_argument("--output-dir", default="data/eval/packs/sample_100")
    parser.add_argument("--thumb-size", type=int, default=180)
    args = parser.parse_args()

    manifest = Path(args.manifest)
    media_dir, items = read_manifest(manifest)
    output_dir = Path(args.output_dir)
    thumbs_dir = output_dir / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for item in items:
        source = Path(item.path)
        if not source.exists():
            source = media_dir / item.relative_path
        thumb_name = f"{Path(item.relative_path).stem}.jpg"
        thumb_path = thumbs_dir / thumb_name
        make_thumbnail(source, args.thumb_size).save(thumb_path, quality=85)
        rows.append({"file": item.name, "relative_path": item.relative_path, "thumb": str(thumb_path)})

    cols = 5
    cell = args.thumb_size + 42
    sheet = Image.new("RGB", (cols * cell, max(1, ((len(rows) + cols - 1) // cols)) * cell), "white")
    draw = ImageDraw.Draw(sheet)
    for idx, row in enumerate(rows):
        x = (idx % cols) * cell
        y = (idx // cols) * cell
        thumb = Image.open(row["thumb"]).convert("RGB")
        sheet.paste(thumb, (x, y))
        draw.text((x + 4, y + args.thumb_size + 4), row["file"][:28], fill="black")
    sheet_path = output_dir / "contact_sheet.jpg"
    sheet.save(sheet_path, quality=90)

    (output_dir / "manifest_summary.json").write_text(
        json.dumps({"count": len(rows), "items": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_eval_template(output_dir / "queries.json", [row["file"] for row in rows])
    print(f"Pacote de avaliacao salvo em: {output_dir}")
    print(f"Contact sheet: {sheet_path}")
    print(f"Edite as consultas em: {output_dir / 'queries.json'}")


if __name__ == "__main__":
    main()
