

def test_catalogue_profile_agrees_with_the_per_column_queries():
    """One pass replaces four scans; it must reach the same numbers."""
    import sqlite3

    import numpy as np

    from core import vector_sidecar

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE memes (id INTEGER PRIMARY KEY, embedding BLOB, desc_embedding BLOB)")
    wide = np.ones(8, dtype=np.float32).tobytes()
    narrow = np.ones(4, dtype=np.float32).tobytes()
    rows = [
        (wide, wide), (wide, None), (wide, narrow), (narrow, narrow),
        (None, None), (b"abc", wide), (b"", None),
    ]
    conn.executemany("INSERT INTO memes (embedding, desc_embedding) VALUES (?, ?)", rows)

    profile = vector_sidecar.catalogue_profile(conn)

    def count(where):
        return conn.execute(f"SELECT COUNT(*) FROM memes WHERE {where}").fetchone()[0]

    assert profile.records == count("1")
    assert profile.embedded == count("embedding IS NOT NULL")
    assert profile.described == count("desc_embedding IS NOT NULL")
    assert profile.image_width == vector_sidecar.dominant_width(conn, "embedding") == 8
    assert profile.desc_width == vector_sidecar.dominant_width(conn, "desc_embedding")
