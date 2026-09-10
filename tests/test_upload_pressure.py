from __future__ import annotations

from scripts.upload_pressure import _JPEG, multipart_parts


def test_synthetic_upload_is_validly_framed_multipart():
    boundary, prefix, suffix = multipart_parts(1024, "pressure.jpg")
    assert boundary.startswith("----iris-load-")
    assert b'name="files"' in prefix
    assert b"low_resource" in suffix
    assert len(_JPEG) < 1024
