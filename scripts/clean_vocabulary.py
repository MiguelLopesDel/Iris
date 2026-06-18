"""Clean up polluted concept names and meme tags created from verbose LLM JSON.

The web-enrichment AI used to return full-sentence "names" (e.g. "Personificação
da bandeira... (São Paulo Countryhumans)") and junk tags (Florence-2 artifacts
like ``VQA>...<loc_0>``), which got stored verbatim as concepts/tags. This script
shortens concept names to a canonical form (merging duplicates) and strips junk
tags from memes.

Dry-run by default; pass --apply to write.

    python scripts/clean_vocabulary.py --db data/iris_v1.db
    python scripts/clean_vocabulary.py --db data/iris_v1.db --apply
"""

from __future__ import annotations

import argparse
import sqlite3

from _path import ensure_project_root

ensure_project_root()

from core.web_enrichment import clean_concept_name, clean_tag_string  # noqa: E402


def plan_concepts(conn: sqlite3.Connection) -> tuple[list[tuple], list[tuple]]:
    """Plan concept name cleanup. Returns (renames, merges):
    renames = [(id, old, new)]; merges = [(dup_id, dup_name, keeper_id, keeper_name)].
    Concepts whose cleaned name collides are merged into a single keeper."""
    rows = conn.execute("SELECT id, name FROM concepts ORDER BY id").fetchall()
    groups: dict[str, list[tuple]] = {}
    for cid, name in rows:
        desired = clean_concept_name(name) or (name or "").strip()
        groups.setdefault(desired.lower(), []).append((cid, name or "", desired))

    renames: list[tuple] = []
    merges: list[tuple] = []
    for members in groups.values():
        # Keeper: one already named exactly as desired, else the lowest id.
        keeper = next((m for m in members if m[1].strip() == m[2]), members[0])
        keeper_id, _, desired = keeper
        for cid, name, _desired in members:
            if cid == keeper_id:
                if name.strip() != desired:
                    renames.append((cid, name, desired))
            else:
                merges.append((cid, name, keeper_id, desired))
    return renames, merges


def apply_concepts(conn: sqlite3.Connection, renames: list[tuple], merges: list[tuple]) -> None:
    for cid, _old, new in renames:
        conn.execute("UPDATE concepts SET name = ? WHERE id = ?", (new, cid))
    for dup_id, _old, keeper_id, _new in merges:
        conn.execute(
            "INSERT OR IGNORE INTO concept_media (concept_id, meme_id, confirmed, added_at) "
            "SELECT ?, meme_id, confirmed, added_at FROM concept_media WHERE concept_id = ?",
            (keeper_id, dup_id),
        )
        conn.execute(
            "UPDATE concept_references SET concept_id = ? WHERE concept_id = ?", (keeper_id, dup_id)
        )
        conn.execute("DELETE FROM concept_media WHERE concept_id = ?", (dup_id,))
        conn.execute("DELETE FROM concepts WHERE id = ?", (dup_id,))


def plan_tags(conn: sqlite3.Connection) -> list[tuple]:
    """Plan tag cleanup. Returns [(meme_id, old, new)] for memes whose tags change."""
    changes: list[tuple] = []
    for mid, tags in conn.execute("SELECT id, tags FROM memes WHERE tags != ''"):
        new = clean_tag_string(tags or "", max_tags=40)
        if new != (tags or "").strip():
            changes.append((mid, tags or "", new))
    return changes


def apply_tags(conn: sqlite3.Connection, changes: list[tuple]) -> None:
    for mid, _old, new in changes:
        conn.execute("UPDATE memes SET tags = ? WHERE id = ?", (new, mid))


def _preview(title: str, rows: list[tuple], fmt, limit: int = 25) -> None:
    print(f"\n{title}: {len(rows)}")
    for row in rows[:limit]:
        print("  " + fmt(row))
    if len(rows) > limit:
        print(f"  ... (+{len(rows) - limit})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Caminho do banco SQLite")
    parser.add_argument("--apply", action="store_true", help="Grava as mudanças (padrão: dry-run)")
    parser.add_argument("--concepts-only", action="store_true")
    parser.add_argument("--tags-only", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    do_concepts = not args.tags_only
    do_tags = not args.concepts_only

    renames: list[tuple] = []
    merges: list[tuple] = []
    tag_changes: list[tuple] = []

    if do_concepts:
        renames, merges = plan_concepts(conn)
        _preview("Renomear conceitos", renames, lambda r: f"#{r[0]}: {r[1]!r} -> {r[2]!r}")
        _preview("Mesclar conceitos (duplicados)", merges,
                 lambda r: f"#{r[0]} {r[1]!r} -> #{r[2]} {r[3]!r}")
    if do_tags:
        tag_changes = plan_tags(conn)
        _preview("Limpar tags", tag_changes, lambda r: f"meme #{r[0]}: {r[1]!r} -> {r[2]!r}")

    if not args.apply:
        print("\n(dry-run) Nada gravado. Rode com --apply para aplicar.")
        return

    if do_concepts:
        apply_concepts(conn, renames, merges)
    if do_tags:
        apply_tags(conn, tag_changes)
    conn.commit()
    print(
        f"\nAplicado: {len(renames)} renomeados, {len(merges)} mesclados, "
        f"{len(tag_changes)} memes com tags limpas."
    )


if __name__ == "__main__":
    main()
