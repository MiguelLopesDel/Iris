from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

import faiss
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

    # Second pass: merge groups whose cluster centroids are mutually similar.
    # matrix is the full normalized engine matrix; item.index values are engine indices,
    # so _merge_by_centroid can index matrix[item.index] correctly.
    duplicate_groups = _merge_by_centroid(duplicate_groups, matrix, threshold)

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

    merged: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        merged[dsu.find(i)].append(i)

    result: list[DuplicateGroup] = []
    for gis in merged.values():
        if len(gis) == 1:
            result.append(groups[gis[0]])
            continue

        # Combine items from all merged groups; recompute scores vs new anchor.
        # No min_direct filter here — items survived the first-pass filter already.
        all_items = sorted(
            [item for gi in gis for item in groups[gi].items],
            key=lambda it: it.index,
        )
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

        if len(new_items) >= 2:
            result.append(
                DuplicateGroup(group_id=0, kind="exact_or_visual", score=min_score, items=new_items)
            )

    return [
        DuplicateGroup(group_id=i + 1, kind=g.kind, score=g.score, items=g.items)
        for i, g in enumerate(result)
    ]


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
        diff = sum(bin(x ^ y).count("1") for x, y in zip(a[:n], b[:n]))
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


def phash_groups(records: list[IndexRecord]) -> dict[str, list[int]]:
    """Group image records by perceptual hash similarity (Hamming distance ≤ 8).

    Detects near-identical copies: resized, recompressed, minor cropped variants.
    Ignores video and audio files — they are handled by CLIP / Chromaprint.
    Returns {representative_key: [local_indices]} for groups with ≥ 2 members.
    """
    def _hamming(a: str, b: str) -> int:
        try:
            return bin(int(a, 16) ^ int(b, 16)).count("1")
        except ValueError:
            return 64

    ph_records: list[tuple[int, str]] = []
    for idx, rec in enumerate(records):
        ph = getattr(rec, "perceptual_hash", "")
        if ph and _media_type(rec.arquivo) == "image":
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

    for i in range(len(ph_records)):
        for j in range(i + 1, len(ph_records)):
            idx_i, ph_i = ph_records[i]
            idx_j, ph_j = ph_records[j]
            if _hamming(ph_i, ph_j) <= 8:
                union(idx_i, idx_j)

    grouped: dict[int, list[int]] = {}
    for idx, _ in ph_records:
        root = find(idx)
        grouped.setdefault(root, []).append(idx)

    return {str(root): indices for root, indices in grouped.items() if len(indices) >= 2}


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
