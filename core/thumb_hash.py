"""Postage-stamp previews that ride along inside record JSON.

A gallery cell has nothing to paint until its thumbnail arrives over the
network — on a home server that is a visible hole per cell for as long as the
round trip takes. This encodes an image small enough to travel inside the same
JSON response that already carries the record's metadata, so a client can paint
an approximate colour field with no extra request and swap in the real
thumbnail when it lands.
"""

from __future__ import annotations

import base64

# 6x6 RGB = 108 bytes -> 144 base64 chars. Upscaled with smoothing on the
# client this reads as a soft colour wash, which is all a placeholder needs.
GRID_SIDE = 6


def encode_thumb_hash(image) -> str:
    """Return a base64 RGB grid for a PIL image, or "" when it cannot be encoded.

    The result is self-describing: a decoder recovers the square's side from the
    decoded byte length (side = sqrt(len / 3)), so changing GRID_SIDE later does
    not break clients already in the field. Never raises — a missing preview
    degrades to no placeholder, which must not fail an import.
    """
    if image is None:
        return ""
    try:
        from PIL import Image

        small = image.convert("RGB").resize((GRID_SIDE, GRID_SIDE), Image.BILINEAR)
        return base64.b64encode(small.tobytes()).decode("ascii")
    except Exception:
        return ""
