"""Read capture metadata from images/videos at import time.

The goal is grouping, not forensics: we extract just enough to suggest collections
(capture date, source app, GPS → city, device). Everything is best-effort and never
raises — a file with no/garbage metadata simply yields empty fields.

Dependencies are all optional/lazy: PIL (already required) for image EXIF, ``ffprobe``
(already used elsewhere) for video tags, and ``reverse_geocoder`` (optional) to turn GPS
into a city label. Without ``reverse_geocoder`` we fall back to a coarse coordinate label.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

IMAGE_EXTS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tiff", ".tif", ".bmp", ".gif"}
)
VIDEO_EXTS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".3gp"})

# EXIF tag ids (IFD0)
_TAG_MAKE = 271
_TAG_MODEL = 272
_TAG_SOFTWARE = 305
_TAG_DATETIME = 306
_IFD_EXIF = 0x8769
_IFD_GPS = 0x8825
_TAG_DATETIME_ORIGINAL = 36867

# Known app fingerprints → canonical label. Matched against EXIF Software / filenames.
_APP_PATTERNS: list[tuple[str, str]] = [
    (r"whatsapp|img-\d{8}-wa", "WhatsApp"),
    (r"telegram", "Telegram"),
    (r"instagram", "Instagram"),
    (r"discord", "Discord"),
    (r"twitter|tweetdeck", "Twitter"),
    (r"snapchat", "Snapchat"),
    (r"tiktok", "TikTok"),
    (r"screenshot|captura|screnshot|screen shot|print", "Captura de tela"),
    (r"photoshop", "Photoshop"),
    (r"gimp", "GIMP"),
    (r"lightroom", "Lightroom"),
]


def extract_metadata(path: str | Path) -> dict:
    """Return ``{captured_at, source_app, device, gps, location_label}`` (best-effort)."""
    path = Path(path)
    ext = path.suffix.lower()
    meta: dict = {
        "captured_at": "",
        "source_app": "",
        "device": "",
        "gps": None,
        "location_label": "",
    }
    try:
        if ext in IMAGE_EXTS:
            _extract_image(path, meta)
        elif ext in VIDEO_EXTS:
            _extract_video(path, meta)
    except Exception:
        pass

    if not meta["source_app"]:
        meta["source_app"] = _app_from_name(path)
    if meta["gps"] and not meta["location_label"]:
        meta["location_label"] = _reverse_geocode(meta["gps"]["lat"], meta["gps"]["lon"])
    return meta


# ── Images ───────────────────────────────────────────────────────────────────


def _extract_image(path: Path, meta: dict) -> None:
    from PIL import Image

    with Image.open(path) as img:
        exif = img.getexif()
    if not exif:
        return

    meta["device"] = _join_device(str(exif.get(_TAG_MAKE, "")), str(exif.get(_TAG_MODEL, "")))
    software = str(exif.get(_TAG_SOFTWARE, "")).strip()
    if software:
        meta["source_app"] = _normalize_app(software)

    captured = ""
    try:
        exif_ifd = exif.get_ifd(_IFD_EXIF)
        captured = _parse_exif_datetime(str(exif_ifd.get(_TAG_DATETIME_ORIGINAL, "")))
    except Exception:
        pass
    meta["captured_at"] = captured or _parse_exif_datetime(str(exif.get(_TAG_DATETIME, "")))

    try:
        gps_ifd = exif.get_ifd(_IFD_GPS)
        meta["gps"] = _gps_from_ifd(gps_ifd)
    except Exception:
        pass


def _gps_from_ifd(gps: dict) -> dict | None:
    # 1/3 = lat/lon ref ('N'/'S'/'E'/'W'); 2/4 = (deg, min, sec) rationals.
    if not gps or 2 not in gps or 4 not in gps:
        return None
    lat = _dms_to_decimal(gps.get(2), str(gps.get(1, "N")))
    lon = _dms_to_decimal(gps.get(4), str(gps.get(3, "E")))
    if lat is None or lon is None:
        return None
    return {"lat": lat, "lon": lon}


def _dms_to_decimal(coord, ref: str) -> float | None:
    try:
        deg, minutes, sec = (float(x) for x in coord)
    except (TypeError, ValueError):
        return None
    decimal = deg + minutes / 60.0 + sec / 3600.0
    if ref.strip().upper() in {"S", "W"}:
        decimal = -decimal
    return round(decimal, 6)


# ── Videos ───────────────────────────────────────────────────────────────────


def _extract_video(path: Path, meta: dict) -> None:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, text=True, timeout=15,
    )
    data = json.loads(result.stdout or "{}")
    tags = {k.lower(): v for k, v in (data.get("format", {}).get("tags", {}) or {}).items()}

    meta["captured_at"] = _parse_iso_datetime(
        tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate") or ""
    )
    meta["device"] = _join_device(
        tags.get("com.apple.quicktime.make", "") or tags.get("make", ""),
        tags.get("com.apple.quicktime.model", "") or tags.get("model", ""),
    )
    encoder = tags.get("encoder") or tags.get("com.apple.quicktime.software") or ""
    if encoder:
        meta["source_app"] = _normalize_app(encoder)
    location = (
        tags.get("com.apple.quicktime.location.iso6709")
        or tags.get("location")
        or tags.get("location-eng")
        or ""
    )
    gps = _parse_iso6709(location)
    if gps:
        meta["gps"] = gps


def _parse_iso6709(value: str) -> dict | None:
    # e.g. "+38.7197-009.1376+010.000/" → lat, lon
    if not value:
        return None
    matches = re.findall(r"[+-]\d+(?:\.\d+)?", value)
    if len(matches) < 2:
        return None
    try:
        return {"lat": round(float(matches[0]), 6), "lon": round(float(matches[1]), 6)}
    except ValueError:
        return None


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _parse_exif_datetime(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        return datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:
        return ""


def _parse_iso_datetime(raw: str) -> str:
    raw = (raw or "").strip().replace("Z", "+00:00")
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=None).isoformat()
    except ValueError:
        # ffmpeg sometimes emits "YYYY-MM-DD HH:MM:SS"
        try:
            return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").isoformat()
        except ValueError:
            return ""


def _join_device(make: str, model: str) -> str:
    make, model = make.strip(), model.strip()
    if make and model and make.lower() not in model.lower():
        return f"{make} {model}"
    return model or make


def _normalize_app(text: str) -> str:
    low = text.lower()
    for pattern, label in _APP_PATTERNS:
        if re.search(pattern, low):
            return label
    # Unknown software string: keep a trimmed first token (e.g. "Adobe").
    return text.strip().split("\x00")[0][:40].strip()


def _app_from_name(path: Path) -> str:
    haystack = f"{path.parent.name} {path.name}".lower()
    for pattern, label in _APP_PATTERNS:
        if re.search(pattern, haystack):
            return label
    return ""


def _reverse_geocode(lat: float, lon: float) -> str:
    """GPS → 'City, CC' via offline reverse_geocoder; coarse coords if unavailable.

    Uses the library's canonical ``search`` entrypoint (single-threaded to stay safe
    inside the indexer's worker context); it caches its own geocoder after the first
    call. Any failure (lib missing, bad coords) degrades to a coordinate label.
    """
    try:
        import reverse_geocoder  # type: ignore[import-untyped]

        hit = reverse_geocoder.search((lat, lon), mode=1, verbose=False)[0]
        city = (hit.get("name") or "").strip()
        country = (hit.get("cc") or "").strip()
        label = ", ".join(part for part in (city, country) if part)
        if label:
            return label
    except Exception:
        pass
    return f"{lat:.3f}, {lon:.3f}"
