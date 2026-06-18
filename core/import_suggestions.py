"""Turn the metadata read at import time into collection suggestions.

Given a set of just-imported memes, group them by capture date, source app, location
and device, then propose a named collection per group (only when enough items share the
value). The user confirms/edits before anything is created — see the post-import panel.
"""
from __future__ import annotations

import json
import sqlite3

MONTHS_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

DEFAULT_MIN_COUNT = 3
DEFAULT_MAX_SUGGESTIONS = 24


def _date_bucket(captured_at: str) -> tuple[str, str] | None:
    """ISO datetime → (sort_key 'YYYY-MM', label 'Junho 2026')."""
    if not captured_at or len(captured_at) < 7:
        return None
    try:
        year = int(captured_at[0:4])
        month = int(captured_at[5:7])
    except ValueError:
        return None
    if not (1 <= month <= 12):
        return None
    return f"{year:04d}-{month:02d}", f"{MONTHS_PT[month]} {year}"


def _name_for(dimension: str, value: str) -> str:
    if dimension == "source_app":
        return "Capturas de tela" if value == "Captura de tela" else f"Mídias do {value}"
    if dimension == "location":
        city = value.split(",")[0].strip() or value
        # With the geocoder this is a city ("Lisboa"); without it, a coordinate string
        # — in which case keep the full label so we don't drop the longitude.
        if not any(ch.isalpha() for ch in city):
            return f"Fotos em {value}"
        return f"Fotos em {city}"
    if dimension == "device":
        return f"Fotos do {value}"
    return value  # date label is already friendly


def suggest_collections(
    conn: sqlite3.Connection,
    *,
    db_ids: list[int] | None = None,
    since: str = "",
    min_count: int = DEFAULT_MIN_COUNT,
    max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
) -> list[dict]:
    """Return suggested collections over the selected memes.

    Scope: explicit ``db_ids`` if given, else memes with ``imported_at >= since``, else
    the whole catalog. Each suggestion: ``{dimension, value, name, count, db_ids}``.
    """
    rows = _fetch_rows(conn, db_ids=db_ids, since=since)

    # dimension -> bucket_key -> {"label": str, "ids": [meme_id]}
    buckets: dict[str, dict[str, dict]] = {
        "date": {}, "source_app": {}, "location": {}, "device": {}
    }
    for meme_id, raw in rows:
        meta = _safe_json(raw)
        if not meta:
            continue
        date = _date_bucket(meta.get("captured_at", ""))
        if date:
            key, label = date
            _add(buckets["date"], key, label, meme_id)
        if meta.get("source_app"):
            _add(buckets["source_app"], meta["source_app"], meta["source_app"], meme_id)
        if meta.get("location_label"):
            _add(buckets["location"], meta["location_label"], meta["location_label"], meme_id)
        if meta.get("device"):
            _add(buckets["device"], meta["device"], meta["device"], meme_id)

    suggestions: list[dict] = []
    for dimension, groups in buckets.items():
        for entry in groups.values():
            if len(entry["ids"]) < min_count:
                continue
            suggestions.append({
                "dimension": dimension,
                "value": entry["label"],
                "name": _name_for(dimension, entry["label"]),
                "count": len(entry["ids"]),
                "db_ids": entry["ids"],
            })

    # Largest, most useful groups first; date sorts newest-first within its key.
    suggestions.sort(key=lambda s: (s["count"], s["dimension"]), reverse=True)
    return suggestions[:max_suggestions]


def _add(group: dict[str, dict], key: str, label: str, meme_id: int) -> None:
    entry = group.setdefault(key, {"label": label, "ids": []})
    entry["ids"].append(meme_id)


def _safe_json(raw: str) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _fetch_rows(
    conn: sqlite3.Connection,
    *,
    db_ids: list[int] | None,
    since: str,
) -> list[tuple[int, str]]:
    if db_ids:
        placeholders = ",".join("?" for _ in db_ids)
        sql = f"SELECT id, metadata_json FROM memes WHERE id IN ({placeholders})"
        params: tuple = tuple(db_ids)
    elif since:
        sql = "SELECT id, metadata_json FROM memes WHERE imported_at >= ?"
        params = (since,)
    else:
        sql = "SELECT id, metadata_json FROM memes"
        params = ()
    try:
        return [(row[0], row[1] or "") for row in conn.execute(sql, params)]
    except sqlite3.OperationalError:
        return []  # metadata_json column not present yet (pre-migration DB)
