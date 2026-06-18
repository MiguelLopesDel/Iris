"""Teste ponta-a-ponta real dos gates de deduplicação (usa modelos de verdade).

Cria imagens, indexa as "originais", depois importa uma cópia exata + uma
re-encodada (near-dup) + uma nova, e mostra o que foi indexado vs. posto em revisão.
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from core import import_review
from core.indexer import IndexerConfig, process_images, resolve_device


def make_image(path: Path, seed: int) -> None:
    img = Image.new("RGB", (256, 256), (seed * 7 % 255, seed * 13 % 255, seed * 29 % 255))
    d = ImageDraw.Draw(img)
    for i in range(0, 256, 16):
        d.line([(i, 0), (255, i)], fill=(seed * 3 % 255, 200, i % 255), width=3)
    d.ellipse([40, 40, 200, 200], outline=(255, 255, 255), width=5)
    d.text((50, 110), f"PATTERN-{seed}", fill=(255, 255, 0))
    img.save(path)


def config_for(media_dir: Path, db: Path) -> IndexerConfig:
    return IndexerConfig(
        media_dir=media_dir, db_path=db, model_name="sentence-transformers/clip-ViT-L-14",
        batch_size=4, device=resolve_device("auto"), recursive=False, limit=None,
        rebuild_faiss_only=False, caption_model="microsoft/Florence-2-large",
        whisper_model="tiny", sample_manifest=None, library_name="default",
        library_root=Path("data/library"), copy_to_library=False,
    )


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="iris-e2e-"))
    db = work / "e2e.db"
    orig = work / "orig"
    incoming = work / "incoming"
    orig.mkdir()
    incoming.mkdir()
    # NOTE: brand-new DB created from scratch by init_db — exercises the fresh-DB schema.

    # Originais que já estarão no banco.
    make_image(orig / "alpha.png", 1)
    make_image(orig / "gamma.png", 99)

    print(">> Indexando originais (alpha, gamma)…", flush=True)
    r1 = process_images(config_for(orig, db))
    print("   indexadas:", r1)

    # Lote de importação: cópia exata, re-encode (near-dup), e uma nova.
    shutil.copy2(orig / "alpha.png", incoming / "alpha_copy.png")          # hash idêntico
    Image.open(orig / "alpha.png").convert("RGB").save(
        incoming / "alpha_reenc.jpg", quality=92                          # near-dup (re-encode)
    )
    make_image(incoming / "delta.png", 250)                               # nova de verdade

    import imagehash
    d = imagehash.phash(Image.open(orig / "alpha.png").convert("RGB")) - imagehash.phash(
        Image.open(incoming / "alpha_reenc.jpg").convert("RGB")
    )
    print(f"   (diag) phash Hamming alpha.png vs alpha_reenc.jpg = {d} (gate ≤ 8)")

    print(">> Importando lote (alpha_copy, alpha_reenc, delta)…", flush=True)
    r2 = process_images(config_for(incoming, db))
    print("   resultado:", r2)

    conn = sqlite3.connect(db)
    print("\n=== memes no banco ===")
    for row in conn.execute("SELECT id, arquivo FROM memes ORDER BY id"):
        print("  ", row[0], row[1])
    print("\n=== fila de revisão (quarentena) ===")
    for row in import_review.list_items(conn):
        print(f"   #{row['id']} {row['detection']:18} cand={Path(row['candidate_path']).name:18}"
              f" match_meme={row['match_meme_id']} score={row['score']}")
    counts = import_review.counts_by_detection(conn)
    conn.close()

    print("\n=== veredito ===")
    ok = (
        counts.get("exact_hash", 0) >= 1
        and counts.get("perceptual", 0) >= 1
        and r2["imported"] == 1
    )
    print("  exact_hash:", counts.get("exact_hash", 0),
          "| perceptual:", counts.get("perceptual", 0),
          "| importadas no 2º lote:", r2["imported"])
    print("  >>>", "PASSOU ✓" if ok else "FALHOU ✗")
    shutil.rmtree(work, ignore_errors=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
