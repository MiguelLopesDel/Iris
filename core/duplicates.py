from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

import faiss
import numpy as np

from core.search_engine import IndexRecord, IrisEngine

_VIDEO_EXTS_DUP = frozenset({".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv"})
_AUDIO_EXTS_DUP = frozenset({".mp3", ".ogg", ".og", ".opus", ".flac", ".wav", ".aac", ".m4a"})


def _media_type(arquivo: str) -> str:
    """Classify a file as 'image', 'video', or 'audio' for duplicate grouping purposes.
    Files of different media types should never be placed in the same duplicate group.
    """
    ext = os.path.splitext(arquivo)[1].lower()
    if ext in _VIDEO_EXTS_DUP:
        return "video"
    if ext in _AUDIO_EXTS_DUP:
        return "audio"
    return "image"


@dataclass(frozen=True)
class DuplicateItem:
    index: int
    arquivo: str
    resolved_path: str | None
    score_to_anchor: float


@dataclass(frozen=True)
class DuplicateGroup:
    group_id: int
    kind: str
    score: float
    items: list[DuplicateItem]


class DisjointSet:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            parent = self.parent[value]
            self.parent[value] = root
            value = parent
        return root

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            self.parent[left_root] = right_root
        elif self.rank[left_root] > self.rank[right_root]:
            self.parent[right_root] = left_root
        else:
            self.parent[right_root] = left_root
            self.rank[left_root] += 1


def find_duplicate_groups(
    engine: IrisEngine,
    *,
    threshold: float = 0.985,
    max_neighbors: int = 50,
    include_exact_hash: bool = True,
    require_existing_files: bool = True,
) -> list[DuplicateGroup]:
    if not engine.records or engine.image_matrix is None:
        return []

    # Only consider records whose files actually exist on disk.
    # Trashed files linger in SQLite until the next sync; without this filter they
    # participate in FAISS clustering and show as broken X images in the UI.
    if require_existing_files:
        live_eng = [
            i for i, r in enumerate(engine.records)
            if r.resolved_path and os.path.exists(r.resolved_path)
        ]
    else:
        live_eng = list(range(len(engine.records)))
    n_live = len(live_eng)
    if n_live < 2:
        return []

    records_live = [engine.records[i] for i in live_eng]

    # Build the full normalized matrix indexed by engine position.
    # Centroid / cosine computations later index into it by engine index, while
    # the FAISS search operates on the live-only slice for performance.
    matrix = np.asarray(engine.image_matrix, dtype=np.float32).copy()
    faiss.normalize_L2(matrix)

    # DSU and pair_scores use live-local indices (0 … n_live-1)
    pair_scores: dict[tuple[int, int], float] = {}
    dsu = DisjointSet(n_live)

    if include_exact_hash:
        # exact_hash_groups returns positions within the passed list → live-local indices
        for indices in exact_hash_groups(records_live).values():
            for base in indices:
                for other in indices:
                    if base >= other:
                        continue
                    dsu.union(base, other)
                    pair_scores[(base, other)] = 1.0

    # pHash grouping for images — Hamming distance ≤ 8 means near-identical copy
    for indices in phash_groups(records_live).values():
        for base in indices:
            for other in indices:
                if base >= other:
                    continue
                dsu.union(base, other)
                pair_scores[(base, other)] = max(0.99, pair_scores.get((base, other), -1.0))

    # Chromaprint fingerprint grouping for audio files
    for indices in chromaprint_groups(records_live).values():
        for base in indices:
            for other in indices:
                if base >= other:
                    continue
                dsu.union(base, other)
                pair_scores[(base, other)] = 1.0

    live_matrix = matrix[live_eng]
    faiss_idx = faiss.IndexFlatIP(live_matrix.shape[1])
    faiss_idx.add(live_matrix)
    limit = min(max_neighbors + 1, n_live)
    scores, neighbors = faiss_idx.search(live_matrix, limit)

    # Pre-compute media types once to avoid repeated os.path.splitext in the inner loop
    media_types = [_media_type(r.arquivo) for r in records_live]

    for row_local, row_neighbors in enumerate(neighbors):
        mt_row = media_types[row_local]
        for nb_pos, nb_local in enumerate(row_neighbors.tolist()):
            if nb_local < 0 or nb_local == row_local:
                continue
            score = float(scores[row_local][nb_pos])
            if score < threshold:
                continue
            # Never group files of different media types (image ≠ video ≠ audio)
            if media_types[nb_local] != mt_row:
                continue
            left, right = sorted((row_local, nb_local))
            dsu.union(left, right)
            pair_scores[(left, right)] = max(score, pair_scores.get((left, right), -1.0))

    grouped: dict[int, list[int]] = defaultdict(list)
    for loc_i in range(n_live):
        grouped[dsu.find(loc_i)].append(loc_i)

    # Post-filter: remove items whose direct cosine to the anchor is too low.
    # This prevents transitive false positives (e.g. a cat photo joining a cluster of
    # black images because it happened to be similar to one dark image in the chain).
    _min_direct = max(threshold - 0.03, 0.90)

    duplicate_groups: list[DuplicateGroup] = []
    for local_indices in grouped.values():
        if len(local_indices) < 2:
            continue
        local_indices = sorted(local_indices)
        anchor_local = local_indices[0]
        anchor_eng = live_eng[anchor_local]
        items: list[DuplicateItem] = []
        group_score = 1.0
        for loc_i in local_indices:
            eng_i = live_eng[loc_i]
            if loc_i == anchor_local:
                score = 1.0
            else:
                left, right = sorted((anchor_local, loc_i))
                score = pair_scores.get((left, right), cosine(matrix[anchor_eng], matrix[eng_i]))
                if score < _min_direct:
                    continue
            group_score = min(group_score, score)
            record = records_live[loc_i]
            items.append(
                DuplicateItem(
                    index=eng_i,  # engine index — used by UI to reference engine.records[idx]
                    arquivo=record.arquivo,
                    resolved_path=record.resolved_path,
                    score_to_anchor=score,
                )
            )
        if len(items) < 2:
            continue

        # Centroid filter: remove items far from the cluster's own centroid.
        # The anchor filter catches items just below threshold, but items connected
        # only by a transitive chain of edges (e.g. a cat photo that joined via one
        # dark image) can still pass that filter while being genuine outliers.
        # Comparing against the centroid (mean embedding of all current members) is
        # more robust: the centroid "points toward" the dense core of the cluster.
        if len(items) >= 3:
            member_eng = [it.index for it in items]  # engine indices
            centroid = matrix[member_eng].mean(axis=0).astype(np.float32)
            c_norm = float(np.linalg.norm(centroid))
            if c_norm > 0:
                centroid /= c_norm
            items = [
                it for it in items
                if it.index == anchor_eng
                or float(np.dot(centroid, matrix[it.index])) >= _min_direct
            ]
            if len(items) < 2:
                continue
            group_score = min(it.score_to_anchor for it in items)

        duplicate_groups.append(
            DuplicateGroup(
                group_id=len(duplicate_groups) + 1,
                kind="exact_or_visual",
                score=group_score,
                items=items,
            )
        )

    # Second pass: consolidate fragments. Two complementary criteria, applied in a
    # loop until the group count stabilises (one merge can expose another):
    #   • _merge_by_centroid  — clusters whose mean embeddings are mutually similar.
    #   • _merge_by_best_pair — clusters whose closest cross-group *item* pair is itself
    #     a duplicate (≥ threshold). The main FAISS pass only inspects each item's top
    #     `max_neighbors` neighbours, so in a dense collection an item's above-threshold
    #     twin in another cluster can fall outside that window and the clusters never get
    #     linked — even though, by the tool's own threshold, they are duplicates.
    # matrix is the full normalized engine matrix; item.index values are engine indices.
    prev_count = -1
    while len(duplicate_groups) != prev_count and len(duplicate_groups) > 1:
        prev_count = len(duplicate_groups)
        duplicate_groups = _merge_by_centroid(duplicate_groups, matrix, threshold)
        duplicate_groups = _merge_by_best_pair(duplicate_groups, matrix, threshold)

    # Split any remaining mixed-type groups (safety net for already-indexed data
    # where the FAISS type filter wasn't applied yet).
    duplicate_groups = _split_by_media_type(duplicate_groups)

    return sorted(
        duplicate_groups,
        key=lambda group: (len(group.items), group.score),
        reverse=True,
    )


def _all_audio_group(group: DuplicateGroup) -> bool:
    """True if every item in the group is an audio file."""
    return all(_media_type(item.arquivo) == "audio" for item in group.items)


def _merge_by_centroid(
    groups: list[DuplicateGroup],
    matrix: np.ndarray,
    threshold: float,
) -> list[DuplicateGroup]:
    """Merge groups whose cluster centroids are mutually similar.

    Anchor-based comparison fails when the two anchors (chosen by smallest index,
    not by centrality) happen to be slightly different from each other. The centroid
    (mean of all normalized member embeddings) is a stable, representative point:
    two clusters of near-identical images will have very close centroids even when
    their individual anchors differ.

    The merge threshold is slightly relaxed (threshold * 0.97) because centroids
    are "pulled toward the average" and thus less extreme than individual points.
    Items merged from a second cluster are NOT re-filtered by min_direct — they
    were already filtered in the main pass and are legitimate group members.
    """
    n = len(groups)
    if n < 2:
        return groups

    # Build L2-normalised centroid for each group
    dim = matrix.shape[1]
    centroids = np.zeros((n, dim), dtype=np.float32)
    for i, g in enumerate(groups):
        idx_arr = np.array([item.index for item in g.items])
        c = matrix[idx_arr].mean(axis=0).astype(np.float32)
        norm = float(np.linalg.norm(c))
        if norm > 0:
            c /= norm
        centroids[i] = c

    ai = faiss.IndexFlatIP(dim)
    ai.add(centroids)
    k = min(50, n)
    # Slightly lower threshold: centroids are "averaged toward center", so two
    # fragmented clusters of near-identical images have centroids that are even
    # more similar than individual points.
    centroid_thresh = threshold * 0.97
    a_scores, a_neighbors = ai.search(centroids, k)

    dsu = DisjointSet(n)
    for i, row in enumerate(a_neighbors):
        for j, nb in enumerate(row.tolist()):
            if nb < 0 or nb == i:
                continue
            if float(a_scores[i][j]) >= centroid_thresh:
                dsu.union(i, nb)

    # Audio-only groups: unconditionally merge them all together.
    # CLIP image embeddings are unreliable for audio files (based on a placeholder image
    # or garbage frames from cv2 opening OGG OPUS files). All audio-only groups should
    # be treated as potentially duplicate regardless of their centroid similarity.
    audio_gis = [i for i, g in enumerate(groups) if _all_audio_group(g)]
    if len(audio_gis) > 1:
        for ai in audio_gis[1:]:
            dsu.union(audio_gis[0], ai)

    return _apply_unions(groups, dsu, matrix)


def _rebuild_group(items: list[DuplicateItem], matrix: np.ndarray) -> DuplicateGroup:
    """Re-anchor a combined item list and recompute scores vs the new anchor.

    No min_direct filter — items being combined already survived the first-pass filter.
    """
    all_items = sorted(items, key=lambda it: it.index)
    anchor_idx = all_items[0].index
    anchor_vec = matrix[anchor_idx]
    new_items: list[DuplicateItem] = []
    min_score = 1.0
    for item in all_items:
        score = 1.0 if item.index == anchor_idx else float(np.dot(anchor_vec, matrix[item.index]))
        min_score = min(min_score, score)
        new_items.append(
            DuplicateItem(
                index=item.index,
                arquivo=item.arquivo,
                resolved_path=item.resolved_path,
                score_to_anchor=score,
            )
        )
    return DuplicateGroup(group_id=0, kind="exact_or_visual", score=min_score, items=new_items)


def _apply_unions(
    groups: list[DuplicateGroup],
    dsu: DisjointSet,
    matrix: np.ndarray,
) -> list[DuplicateGroup]:
    """Collapse ``groups`` according to ``dsu`` (one entry per group), rebuilding merged
    clusters and renumbering group ids."""
    merged: dict[int, list[int]] = defaultdict(list)
    for i in range(len(groups)):
        merged[dsu.find(i)].append(i)

    result: list[DuplicateGroup] = []
    for gis in merged.values():
        if len(gis) == 1:
            result.append(groups[gis[0]])
            continue
        combined = _rebuild_group(
            [item for gi in gis for item in groups[gi].items], matrix
        )
        if len(combined.items) >= 2:
            result.append(combined)

    return [
        DuplicateGroup(group_id=i + 1, kind=g.kind, score=g.score, items=g.items)
        for i, g in enumerate(result)
    ]


def _merge_by_best_pair(
    groups: list[DuplicateGroup],
    matrix: np.ndarray,
    threshold: float,
) -> list[DuplicateGroup]:
    """Single-linkage completion: merge two groups whose closest cross-group pair of
    items is itself a duplicate (cosine ≥ ``threshold``).

    The main FAISS pass only inspects each item's top ``max_neighbors`` neighbours, so in
    a dense collection an item's above-threshold match in *another* cluster can fall
    outside that window and the clusters never get linked. Centroid merging doesn't
    rescue every such case: when the two clusters' means are pulled apart by their other
    members, the centroids stay distant even though one cross pair is near-identical.

    This pass re-checks neighbours over only the already-grouped items — a small set, so
    an effectively unbounded ``k`` is affordable — and links the groups the top-k window
    missed. It adds no edge below ``threshold``, so it cannot create matches the
    threshold wouldn't already allow.
    """
    n = len(groups)
    if n < 2:
        return groups

    item_eng: list[int] = []
    item_group: list[int] = []
    item_type: list[str] = []
    for gi, g in enumerate(groups):
        for it in g.items:
            item_eng.append(it.index)
            item_group.append(gi)
            item_type.append(_media_type(it.arquivo))
    if len(item_eng) < 2:
        return groups

    sub = matrix[np.array(item_eng)]
    idx = faiss.IndexFlatIP(sub.shape[1])
    idx.add(sub)
    k = min(len(item_eng), 256)  # unbounded enough for the grouped subset
    scores, neighbors = idx.search(sub, k)

    dsu = DisjointSet(n)
    for p, row in enumerate(neighbors):
        gp = item_group[p]
        for jpos, q in enumerate(row.tolist()):
            if q < 0 or q == p:
                continue
            if float(scores[p][jpos]) < threshold:
                break  # neighbours are sorted by descending score
            if item_group[q] == gp:
                continue
            # Never bridge different media types (image ≠ video ≠ audio).
            if item_type[q] != item_type[p]:
                continue
            dsu.union(gp, item_group[q])

    return _apply_unions(groups, dsu, matrix)


def _split_by_media_type(groups: list[DuplicateGroup]) -> list[DuplicateGroup]:
    """Split any group that contains files of different media types.

    Needed as a safety net for already-indexed data where the type filter wasn't
    applied during the FAISS search pass. Also catches any cross-type connections
    that may survive the merge phase (e.g. an MP4 whose frame is visually identical
    to a PNG, which gets score 1.000 via exact-hash or near-identical CLIP embedding).
    """
    result: list[DuplicateGroup] = []
    for group in groups:
        by_type: dict[str, list[DuplicateItem]] = defaultdict(list)
        for item in group.items:
            by_type[_media_type(item.arquivo)].append(item)
        if len(by_type) == 1:
            result.append(group)
            continue
        for type_items in by_type.values():
            if len(type_items) < 2:
                continue
            min_score = min(it.score_to_anchor for it in type_items)
            result.append(
                DuplicateGroup(group_id=0, kind=group.kind, score=min_score, items=type_items)
            )
    return [
        DuplicateGroup(group_id=i + 1, kind=g.kind, score=g.score, items=g.items)
        for i, g in enumerate(result)
    ]


def chromaprint_groups(records: list[IndexRecord]) -> dict[str, list[int]]:
    """Group records whose Chromaprint fingerprints are similar (normalized Hamming < 0.2).

    Returns groups in the same format as exact_hash_groups: {representative_key: [indices]}.
    Only considers records with non-empty audio_fingerprint.
    """
    import base64
    import struct

    def _decode(fp: str) -> list[int] | None:
        try:
            data = base64.b64decode(fp)
            n = len(data) // 4
            return list(struct.unpack(f">{n}I", data[:n * 4])) if n > 0 else None
        except Exception:
            return None

    def _hamming(a: list[int], b: list[int]) -> float:
        n = min(len(a), len(b))
        if n == 0:
            return 1.0
        diff = sum(bin(x ^ y).count("1") for x, y in zip(a[:n], b[:n], strict=False))
        return diff / (32 * n)

    # Collect records with fingerprints
    fp_records: list[tuple[int, str, list[int]]] = []
    for idx, rec in enumerate(records):
        fp = getattr(rec, "audio_fingerprint", "")
        if not fp:
            continue
        decoded = _decode(fp)
        if decoded:
            fp_records.append((idx, fp, decoded))

    if len(fp_records) < 2:
        return {}

    # Union-Find for fingerprint groups
    parent = {idx: idx for idx, _, _ in fp_records}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    # O(n²) comparison — acceptable for typical audio counts in a collection
    for i in range(len(fp_records)):
        for j in range(i + 1, len(fp_records)):
            idx_i, _, dec_i = fp_records[i]
            idx_j, _, dec_j = fp_records[j]
            if _hamming(dec_i, dec_j) < 0.20:
                union(idx_i, idx_j)

    grouped: dict[int, list[int]] = {}
    for idx, _, _ in fp_records:
        root = find(idx)
        grouped.setdefault(root, []).append(idx)

    return {str(root): indices for root, indices in grouped.items() if len(indices) >= 2}


_PHASH_MAX_DISTANCE = 8
_PHASH_BITS = 64
# Number of bit slices the hash is split into for indexing. Three is what makes
# the index sparse: see _phash_probe_masks.
_PHASH_SLICES = 3


def _phash_probe_masks(width: int, radius: int) -> list[int]:
    """XOR masks that flip up to ``radius`` bits of a slice.

    Precomputed per slice width because the offsets are the same for every hash;
    building them inside the scan made them the dominant cost.
    """
    masks = [0]
    for flips in range(1, radius + 1):
        for positions in combinations(range(width), flips):
            mask = 0
            for position in positions:
                mask ^= 1 << position
            masks.append(mask)
    return masks


def _phash_linking_pairs(hashes: list[str], max_distance: int) -> list[tuple[int, int]]:
    """Position pairs that connect exactly the groups every close pair would.

    Deliberately not *all* close pairs. Grouping is union-find, which needs
    enough edges to connect each component, not every edge inside it. The
    distinction is the difference between linear and quadratic on real
    libraries: blank screenshots and solid-colour images share one hash, and
    5,000 of them produce 12.5 million identical-pair edges to build a single
    group. Collapsing equal hashes first turns that into 5,000 edges.

    Candidate selection is multi-index hashing. Split each hash into
    ``_PHASH_SLICES`` slices: if two hashes differ by at most ``max_distance``
    bits in total, some slice carries at most
    ``max_distance // _PHASH_SLICES`` of those differences, because they cannot
    all exceed their share. Indexing each slice and probing every value within
    that small per-slice radius therefore misses no pair, and the exact distance
    check then discards the rest.

    Both directions stated plainly, because they are easy to invert: within the
    radius this loses **nothing** -- the pigeonhole makes that a proof, not a
    measurement -- and it produces spurious candidates that the popcount
    rejects. It is the exactness of the final check, not the index, that keeps
    unrelated hashes apart.

    And that check is exact about the stored fingerprints, not about the images.
    Two photographs can sit within eight bits of each other and not be the same
    picture; solid colours used to do exactly that, which is why they no longer
    reach this function at all.

    Three slices of ~21 bits give two million buckets each, which stays sparse
    well past any realistic library, so a probe usually lands on nothing and the
    cost tracks the number of distinct hashes rather than the number of pairs.

    A tree is the obvious alternative and it lost badly in the one measurement
    made here, for a reason specific to this metric rather than to trees. Two unrelated 64-bit hashes
    are almost always about 32 bits apart, so a radius of 8 admits nearly every
    branch under the triangle inequality and the prune collapses. Measured on
    this data, a BK-tree visited about half its nodes per lookup: roughly 160
    seconds at 25k hashes against 0.8 here.

    Where this stops working, stated so nobody has to rediscover it. Slicing
    reduces the pairs examined, it does not change their order of growth: a
    random pair becomes a candidate with probability 2.8e-4, so the candidate
    count is still proportional to the square of the catalogue. Measured, the
    candidates per hash double whenever the catalogue doubles -- 7 at 25k, 28 at
    100k, 112 at 400k -- and only 27 of the 45 million candidates at 400k were
    real pairs. The constant is roughly 3,500x better than all-pairs: 0.9 s at
    25k, seconds at 100k, minutes at a million, hours beyond that.

    That matters, because one account is not one person. A studio that edits
    images for a living accumulates hundreds of thousands to millions of files
    in a single library, and an ordinary phone user reaches tens of thousands
    in a few years. Sharding per account does not bound this the way it bounds
    storage. Treat the numbers above as the budget this index actually has:
    past roughly a million hashes in one catalogue it needs replacing, not
    tuning. What that replacement should be is open: a wider fingerprint, a
    probabilistic index, or another signal entirely. The measurements here rule
    out this configuration at that size, not every configuration.

    The diagnostic that reveals it early, without waiting for a huge benchmark:
    plot candidates per hash against catalogue size. Flat means the index scales;
    proportional means quadratic, whatever the wall-clock says at small sizes.
    """
    positions_by_value: dict[int, list[int]] = {}
    for position, digest in enumerate(hashes):
        positions_by_value.setdefault(int(digest, 16), []).append(position)

    pairs: list[tuple[int, int]] = []
    for shared in positions_by_value.values():
        # A chain is enough to put identical hashes in one group.
        pairs.extend((shared[0], other) for other in shared[1:])

    values = list(positions_by_value)
    representative = [positions_by_value[value][0] for value in values]
    radius = max_distance // _PHASH_SLICES
    edges = [(index * _PHASH_BITS) // _PHASH_SLICES for index in range(_PHASH_SLICES + 1)]

    linked: set[tuple[int, int]] = set()
    for slice_index in range(_PHASH_SLICES):
        start, end = edges[slice_index], edges[slice_index + 1]
        shift = _PHASH_BITS - end
        mask = (1 << (end - start)) - 1
        table: dict[int, list[int]] = {}
        for index, value in enumerate(values):
            table.setdefault((value >> shift) & mask, []).append(index)

        # Hoisted: the masks depend only on the slice width, and rebuilding them
        # per bucket cost more than the rest of the scan put together.
        probes = _phash_probe_masks(end - start, radius)
        for key, members in table.items():
            for probe in probes:
                neighbours = table.get(key ^ probe)
                if not neighbours:
                    continue
                same_bucket = probe == 0
                for left in members:
                    for right in neighbours:
                        # Within one bucket every pair would be visited twice.
                        if same_bucket and right <= left:
                            continue
                        edge = (left, right) if left < right else (right, left)
                        if edge in linked:
                            continue
                        if bin(values[left] ^ values[right]).count("1") <= max_distance:
                            linked.add(edge)
    pairs.extend(
        (representative[left], representative[right]) for left, right in sorted(linked)
    )
    return pairs


def phash_groups(records: list[IndexRecord]) -> dict[str, list[int]]:
    """Group image records by perceptual hash similarity (Hamming distance ≤ 8).

    Detects near-identical copies: resized, recompressed, minor cropped variants.
    Ignores video and audio files — they are handled by CLIP / Chromaprint.
    Returns {representative_key: [local_indices]} for groups with ≥ 2 members.

    This compared every pair, which is fine until it is not: the cost grows with
    the square of the library, so 3.6k hashes took 2.7 s and the 25k-per-user
    target of the sync design would have taken minutes. Candidates now come from
    matching bit slices (see ``_phash_candidate_buckets``) and the distance is
    still measured exactly, so the groups are unchanged — 38x faster on the real
    catalog, 25k hashes in under two seconds.
    """
    ph_records: list[tuple[int, str]] = []
    for idx, rec in enumerate(records):
        ph = getattr(rec, "perceptual_hash", "")
        # Only full-width hashes take part. Non-hex text already never matched
        # (it scored distance 64), but a short hex string used to be widened
        # with leading zeros and compared anyway, so "1" could be grouped with
        # "0000000000000001" -- two hashes of different bit widths are not
        # comparable, and every hash this project writes is 16 characters.
        if ph and _media_type(rec.arquivo) == "image" and _is_phash(ph):
            ph_records.append((idx, ph))

    if len(ph_records) < 2:
        return {}

    parent = {idx: idx for idx, _ in ph_records}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    hashes = [digest for _, digest in ph_records]
    for left, right in _phash_linking_pairs(hashes, _PHASH_MAX_DISTANCE):
        union(ph_records[left][0], ph_records[right][0])

    grouped: dict[int, list[int]] = {}
    for idx, _ in ph_records:
        root = find(idx)
        grouped.setdefault(root, []).append(idx)

    return {str(root): indices for root, indices in grouped.items() if len(indices) >= 2}


def _is_phash(value: str) -> bool:
    if len(value) != _PHASH_BITS // 4:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def exact_hash_groups(records: list[IndexRecord]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for idx, record in enumerate(records):
        content_hash = getattr(record, "content_hash", "")
        if content_hash:
            groups[content_hash].append(idx)
    return {key: value for key, value in groups.items() if len(value) > 1}


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denom == 0:
        return 0.0
    return float(np.dot(left, right) / denom)
