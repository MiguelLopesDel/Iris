"""The graph has to answer questions about items that no longer exist.

The failure it exists to prevent: a perceptual hash links A and B, the user keeps
A and deletes B, and a later crop-aware detector would have linked B and C. Run
that detector on the survivors and it sees only A and C, which it cannot link.
The evidence lived in B.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.equivalence_graph import (
    KIND_CONTENT,
    KIND_PHASH,
    PROVENANCE_EXACT_CONTENT,
    PROVENANCE_IMPORT_QUARANTINE,
    PROVENANCE_USER_CONFIRMED,
    add_fingerprints,
    assert_equivalent,
    component_of,
    edges_of,
    fingerprints_of,
    mark_removed,
    member_for_fingerprint,
    members_of_component,
    record_member,
)

CROP = "crop_segments"


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def test_a_new_member_is_its_own_component(conn):
    member = record_member(conn, content_hash="aaa")

    assert component_of(conn, member) == member
    assert [row["content_hash"] for row in members_of_component(conn, member)] == ["aaa"]


def test_registering_the_same_file_twice_does_not_duplicate_it(conn):
    first = record_member(conn, content_hash="aaa", media_id=1)
    second = record_member(conn, content_hash="aaa", original_path="/x/a.jpg")

    assert first == second
    assert members_of_component(conn, first)[0]["original_path"] == "/x/a.jpg"
    assert members_of_component(conn, first)[0]["media_id"] == 1


def test_a_later_detector_enriches_an_existing_member(conn):
    """Adding a new kind of evidence must not replace what is already there."""
    member = record_member(conn, content_hash="aaa", fingerprints={KIND_PHASH: "ff00"})

    add_fingerprints(conn, member, {CROP: "seg1,seg2"})

    stored = fingerprints_of(conn, member)
    assert stored[KIND_PHASH] == ["ff00"]
    assert stored[CROP] == ["seg1,seg2"]
    assert stored[KIND_CONTENT] == ["aaa"]


def test_linking_merges_components_and_is_stable(conn):
    a = record_member(conn, content_hash="aaa")
    b = record_member(conn, content_hash="bbb")

    surviving = assert_equivalent(conn, a, b, provenance=PROVENANCE_USER_CONFIRMED)

    # The older component absorbs the newer, so the result does not depend on
    # the order detectors happen to run in.
    assert component_of(conn, a) == component_of(conn, b) == surviving
    assert len(members_of_component(conn, b)) == 2


def test_linking_is_transitive_across_separate_calls(conn):
    a = record_member(conn, content_hash="aaa")
    b = record_member(conn, content_hash="bbb")
    c = record_member(conn, content_hash="ccc")

    assert_equivalent(conn, a, b, provenance=PROVENANCE_USER_CONFIRMED)
    assert_equivalent(conn, b, c, provenance=PROVENANCE_USER_CONFIRMED)

    assert component_of(conn, a) == component_of(conn, c)
    assert len(members_of_component(conn, c)) == 3


def test_linking_an_unknown_member_is_refused(conn):
    a = record_member(conn, content_hash="aaa")

    with pytest.raises(ValueError):
        assert_equivalent(conn, a, 999, provenance=PROVENANCE_USER_CONFIRMED)


def test_removal_keeps_the_member_and_its_evidence(conn):
    member = record_member(
        conn, content_hash="bbb", fingerprints={KIND_PHASH: "ff00"}, media_id=7
    )

    assert mark_removed(conn, ["bbb"], "2026-09-11T00:00:00Z") == 1

    row = members_of_component(conn, member)[0]
    assert row["removed_at"] == "2026-09-11T00:00:00Z"
    assert row["media_id"] is None, "o vínculo com o catálogo deve sair"
    assert fingerprints_of(conn, member)[KIND_PHASH] == ["ff00"], "a evidência não pode sumir"


def test_removing_twice_does_not_rewrite_the_first_timestamp(conn):
    record_member(conn, content_hash="bbb")
    mark_removed(conn, ["bbb"], "2026-09-11T00:00:00Z")

    assert mark_removed(conn, ["bbb"], "2026-10-01T00:00:00Z") == 0


def test_a_deleted_member_bridges_two_detectors(conn):
    """The whole reason this module exists.

    phash links A and B. B is deleted. A crop detector later recognises C as
    matching B's crop fingerprint -- evidence that only survives because it was
    written before B was removed. C must end up in A's component.
    """
    a = record_member(conn, content_hash="aaa", fingerprints={KIND_PHASH: "ff00"})
    b = record_member(
        conn,
        content_hash="bbb",
        fingerprints={KIND_PHASH: "ff01", CROP: "segX,segY"},
    )
    assert_equivalent(conn, a, b, provenance=PROVENANCE_USER_CONFIRMED)
    mark_removed(conn, ["bbb"], "2026-09-11T00:00:00Z")

    # Much later: a detector that did not exist then computes a crop fingerprint
    # for a file it has never seen, and finds it was already known.
    c = record_member(conn, content_hash="ccc", fingerprints={CROP: "segX,segY"})
    bridged = member_for_fingerprint(conn, CROP, "segX,segY")
    assert bridged == b, "a ponte tinha de vir do membro apagado"
    assert_equivalent(conn, c, bridged, provenance=PROVENANCE_USER_CONFIRMED)

    assert component_of(conn, c) == component_of(conn, a)
    hashes = sorted(row["content_hash"] for row in members_of_component(conn, a))
    assert hashes == ["aaa", "bbb", "ccc"]


def test_without_the_deleted_member_the_bridge_is_lost(conn):
    """States the cost of not recording evidence before deletion.

    Same situation as above, except B's crop fingerprint was never written. The
    later detector finds nothing, and A and C stay apart for good.
    """
    a = record_member(conn, content_hash="aaa", fingerprints={KIND_PHASH: "ff00"})
    b = record_member(conn, content_hash="bbb", fingerprints={KIND_PHASH: "ff01"})
    assert_equivalent(conn, a, b, provenance=PROVENANCE_USER_CONFIRMED)
    mark_removed(conn, ["bbb"], "2026-09-11T00:00:00Z")

    c = record_member(conn, content_hash="ccc", fingerprints={CROP: "segX,segY"})

    assert member_for_fingerprint(conn, CROP, "segX,segY") == c
    assert component_of(conn, c) != component_of(conn, a)


def test_a_fingerprint_lookup_misses_cleanly(conn):
    record_member(conn, content_hash="aaa", fingerprints={KIND_PHASH: "ff00"})

    assert member_for_fingerprint(conn, CROP, "nada") is None


def test_the_component_survives_the_removal_of_every_file(conn):
    """Even with nothing left on disk, the equivalences are still known."""
    a = record_member(conn, content_hash="aaa", fingerprints={KIND_PHASH: "ff00"})
    b = record_member(conn, content_hash="bbb", fingerprints={KIND_PHASH: "ff01"})
    assert_equivalent(conn, a, b, provenance=PROVENANCE_USER_CONFIRMED)

    mark_removed(conn, ["aaa", "bbb"], "2026-09-11T00:00:00Z")

    assert len(members_of_component(conn, a)) == 2
    assert member_for_fingerprint(conn, KIND_PHASH, "ff01") == b


# ── A fiação, não só o módulo ────────────────────────────────────────────────


def test_the_trash_endpoint_records_evidence_before_deleting(tmp_path, monkeypatch):
    """The mechanism existing is not the same as the mechanism being called.

    `core.deleted_registry.register_deleted` already computed a perceptual hash
    before deletion, and no server route ever invoked it, so every deletion in
    production was already discarding its evidence. Unit tests of the module
    cannot catch that; this asserts the endpoint itself does the recording.
    """
    import asyncio

    import httpx

    import server
    from core.search_types import IndexRecord

    doomed = tmp_path / "photo.jpg"
    doomed.write_bytes(b"bytes")
    record = IndexRecord(
        index=0,
        db_id=1,
        arquivo="photo.jpg",
        caminho=str(doomed),
        texto_extraido="",
        descricao_ia="",
        tags="",
        embedding=None,
        desc_embedding=None,
        resolved_path=str(doomed),
        content_hash="abc123",
        perceptual_hash="ff00ff00ff00ff00",
    )
    # check_same_thread mirrors production (core/db_manager), because the
    # recording runs in a threadpool.
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    backend = type(
        "Backend",
        (),
        {
            "get_all_records": staticmethod(lambda: [record]),
            "get_record": staticmethod(lambda index: [record][index]),
            "engine": object(),
        },
    )()

    monkeypatch.setattr(server, "_get_backend", lambda: backend)
    monkeypatch.setattr(server, "_backend_connection", lambda *a, **k: connection)
    monkeypatch.setattr(server, "maybe_auto_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(server, "move_to_trash", lambda paths: (list(paths), []))
    server._invalidate_view_caches()

    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/trash", data={"db_ids": "1"})

    try:
        response = asyncio.run(run())
    finally:
        server._invalidate_view_caches()

    assert response.status_code == 200
    member = member_for_fingerprint(connection, KIND_PHASH, "ff00ff00ff00ff00")
    assert member is not None, "a evidência perceptual foi perdida na remoção"
    assert member_for_fingerprint(connection, KIND_CONTENT, "abc123") == member
    row = members_of_component(connection, member)[0]
    assert row["removed_at"], "o membro não foi marcado como removido"
    assert row["media_id"] is None
    connection.close()


def test_deletion_is_refused_when_the_evidence_cannot_be_recorded(tmp_path, monkeypatch):
    """Failing to record must stop the deletion, not be logged and ignored.

    Not freeing space is recoverable; destroying the only copy of the evidence
    is not. The first version of this code caught the error, warned, and let the
    endpoint answer "moved: 1" — reporting success for the one outcome the graph
    exists to prevent.
    """
    import asyncio

    import httpx

    import server
    from core.search_types import IndexRecord

    doomed = tmp_path / "photo.jpg"
    doomed.write_bytes(b"bytes")
    record = IndexRecord(
        index=0, db_id=1, arquivo="photo.jpg", caminho=str(doomed),
        texto_extraido="", descricao_ia="", tags="", embedding=None,
        desc_embedding=None, resolved_path=str(doomed), content_hash="abc123",
    )
    # With an engine present there *is* a catalogue to record into, so a write
    # failure is data loss rather than "this deployment has no graph".
    backend = type(
        "Backend",
        (),
        {
            "get_all_records": staticmethod(lambda: [record]),
            "get_record": staticmethod(lambda index: [record][index]),
            "engine": object(),
        },
    )()
    trashed: list[str] = []

    def exploding_connection(*args, **kwargs):
        raise sqlite3.OperationalError("disco cheio")

    monkeypatch.setattr(server, "_get_backend", lambda: backend)
    monkeypatch.setattr(server, "_backend_connection", exploding_connection)
    monkeypatch.setattr(server, "maybe_auto_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(server, "move_to_trash", lambda paths: (trashed.extend(paths), []))
    server._invalidate_view_caches()

    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/trash", data={"db_ids": "1"})

    try:
        response = asyncio.run(run())
    finally:
        server._invalidate_view_caches()

    assert response.status_code == 503
    assert trashed == [], "o arquivo foi para a lixeira apesar de a evidência ter falhado"
    assert doomed.exists()


def _record(tmp_path, db_id, name, content_hash, phash):
    from core.search_types import IndexRecord

    path = tmp_path / name
    path.write_bytes(b"bytes")
    return IndexRecord(
        index=db_id - 1, db_id=db_id, arquivo=name, caminho=str(path),
        texto_extraido="", descricao_ia="", tags="", embedding=None,
        desc_embedding=None, resolved_path=str(path), content_hash=content_hash,
        perceptual_hash=phash,
    )


def _trash(server, records, db_ids, connection, monkeypatch):
    import asyncio

    import httpx

    backend = type(
        "Backend",
        (),
        {
            "get_all_records": staticmethod(lambda: records),
            "get_record": staticmethod(lambda index: records[index]),
            "engine": object(),
        },
    )()
    monkeypatch.setattr(server, "_get_backend", lambda: backend)
    monkeypatch.setattr(server, "_backend_connection", lambda *a, **k: connection)
    monkeypatch.setattr(server, "maybe_auto_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(server, "move_to_trash", lambda paths: (list(paths), []))
    server._invalidate_view_caches()

    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/trash", data={"db_ids": db_ids})

    try:
        return asyncio.run(run())
    finally:
        server._invalidate_view_caches()


def test_deleting_near_identical_photos_asserts_no_equivalence(tmp_path, monkeypatch):
    """Deleting is not declaring, and the interface never asked the question.

    The trash button asks only "move to the trash?". An earlier version inferred
    equivalence from "B was deleted while A survived within eight bits" and wrote
    a permanent edge. On a burst of near-identical frames -- the most common
    group in a real library -- deleting the blurry one would have declared the
    whole burst to be one picture, and every later detector would have inherited
    that as fact.
    """
    import server

    keeper = _record(tmp_path, 1, "keep.jpg", "aaa", "ff00ff00ff00ff00")
    doomed = _record(tmp_path, 2, "blurry.jpg", "bbb", "ff00ff00ff00ff01")
    connection = sqlite3.connect(":memory:", check_same_thread=False)

    response = _trash(server, [keeper, doomed], "2", connection, monkeypatch)

    assert response.status_code == 200
    removed = member_for_fingerprint(connection, KIND_CONTENT, "bbb")
    assert removed is not None, "a evidência tem de ser gravada"
    assert fingerprints_of(connection, removed)[KIND_PHASH] == ["ff00ff00ff00ff01"]
    assert edges_of(connection, removed) == [], "gravou equivalência que ninguém afirmou"
    assert [row["content_hash"] for row in members_of_component(connection, removed)] == ["bbb"]
    connection.close()


def test_deleting_a_whole_group_does_not_link_it_to_itself_only(tmp_path, monkeypatch):
    """When every copy goes, the members are still recorded for later bridges."""
    import server

    first = _record(tmp_path, 1, "a.jpg", "aaa", "ff00ff00ff00ff00")
    second = _record(tmp_path, 2, "b.jpg", "bbb", "ff00ff00ff00ff01")
    connection = sqlite3.connect(":memory:", check_same_thread=False)

    response = _trash(server, [first, second], "1,2", connection, monkeypatch)

    assert response.status_code == 200
    for content_hash in ("aaa", "bbb"):
        member = member_for_fingerprint(connection, KIND_CONTENT, content_hash)
        assert member is not None
        assert members_of_component(connection, member)[0]["removed_at"]
    connection.close()


# ── Identidade da componente e proveniência ──────────────────────────────────


def test_a_component_survives_losing_every_member_file(conn):
    """Component identity cannot be a surviving member.

    Naming the component after one of its files breaks the moment all of them
    are deleted -- and deleting every copy is an ordinary outcome, not an edge
    case.
    """
    a = record_member(conn, content_hash="aaa", fingerprints={KIND_PHASH: "ff00"})
    b = record_member(conn, content_hash="bbb", fingerprints={KIND_PHASH: "ff01"})
    assert_equivalent(conn, a, b, provenance=PROVENANCE_USER_CONFIRMED)
    before = component_of(conn, a)

    mark_removed(conn, ["aaa", "bbb"], "2026-09-11T00:00:00Z")

    assert component_of(conn, a) == before
    assert len(members_of_component(conn, a)) == 2


def test_an_edge_records_who_asserted_it(conn):
    a = record_member(conn, content_hash="aaa")
    b = record_member(conn, content_hash="bbb")

    assert_equivalent(
        conn, a, b,
        provenance=PROVENANCE_IMPORT_QUARANTINE,
        detector_version="phash-v1",
        confirmed_at="2026-09-11T00:00:00Z",
    )

    edge = edges_of(conn, a)[0]
    assert edge["provenance"] == PROVENANCE_IMPORT_QUARANTINE
    assert edge["detector_version"] == "phash-v1"


def test_the_same_pair_can_be_asserted_by_more_than_one_source(conn):
    """A person confirming what a detector already suggested is not a duplicate row.

    Keeping both is what later allows a whole class of assertion to be
    reconsidered without losing the others.
    """
    a = record_member(conn, content_hash="aaa")
    b = record_member(conn, content_hash="bbb")

    assert_equivalent(conn, a, b, provenance=PROVENANCE_IMPORT_QUARANTINE)
    assert_equivalent(conn, a, b, provenance=PROVENANCE_USER_CONFIRMED)

    assert sorted(edge["provenance"] for edge in edges_of(conn, a)) == [
        PROVENANCE_IMPORT_QUARANTINE,
        PROVENANCE_USER_CONFIRMED,
    ]


def test_an_edge_is_kept_even_when_it_merges_nothing(conn):
    """The assertion is the record, not its effect on the components."""
    a = record_member(conn, content_hash="aaa")
    b = record_member(conn, content_hash="bbb")
    assert_equivalent(conn, a, b, provenance=PROVENANCE_USER_CONFIRMED)

    assert_equivalent(conn, b, a, provenance=PROVENANCE_EXACT_CONTENT)

    assert len(edges_of(conn, a)) == 2


def test_fingerprints_carry_a_version(conn):
    """A fingerprint's meaning changes when its algorithm does."""
    member = record_member(conn, content_hash="aaa")
    add_fingerprints(conn, member, {KIND_PHASH: "ff00"}, version="2", bits=256)

    rows = conn.execute(
        "SELECT version, bits FROM equivalence_fingerprints"
        " WHERE member_id = ? AND kind = ? AND value = ?",
        (member, KIND_PHASH, "ff00"),
    ).fetchone()
    assert rows == ("2", 256)
