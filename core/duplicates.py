from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import faiss
import numpy as np

from core.search_engine import IndexRecord, MemeSearchEngine


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
    engine: MemeSearchEngine,
    *,
    threshold: float = 0.985,
    max_neighbors: int = 12,
    include_exact_hash: bool = True,
) -> list[DuplicateGroup]:
    if not engine.records or engine.image_matrix is None:
        return []

    pair_scores: dict[tuple[int, int], float] = {}
    dsu = DisjointSet(len(engine.records))

    if include_exact_hash:
        for indices in exact_hash_groups(engine.records).values():
            for base in indices:
                for other in indices:
                    if base >= other:
                        continue
                    dsu.union(base, other)
                    pair_scores[(base, other)] = 1.0

    matrix = np.asarray(engine.image_matrix, dtype=np.float32).copy()
    faiss.normalize_L2(matrix)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)
    limit = min(max_neighbors + 1, len(engine.records))
    scores, neighbors = index.search(matrix, limit)

    for row_idx, row_neighbors in enumerate(neighbors):
        for local_pos, neighbor_idx in enumerate(row_neighbors.tolist()):
            if neighbor_idx < 0 or neighbor_idx == row_idx:
                continue
            score = float(scores[row_idx][local_pos])
            if score < threshold:
                continue
            left, right = sorted((row_idx, neighbor_idx))
            dsu.union(left, right)
            pair_scores[(left, right)] = max(score, pair_scores.get((left, right), -1.0))

    grouped: dict[int, list[int]] = defaultdict(list)
    for idx in range(len(engine.records)):
        grouped[dsu.find(idx)].append(idx)

    duplicate_groups: list[DuplicateGroup] = []
    for indices in grouped.values():
        if len(indices) < 2:
            continue
        indices = sorted(indices)
        anchor = indices[0]
        items: list[DuplicateItem] = []
        group_score = 1.0
        for idx in indices:
            if idx == anchor:
                score = 1.0
            else:
                left, right = sorted((anchor, idx))
                score = pair_scores.get((left, right), cosine(matrix[anchor], matrix[idx]))
            group_score = min(group_score, score)
            record = engine.records[idx]
            items.append(
                DuplicateItem(
                    index=idx,
                    arquivo=record.arquivo,
                    resolved_path=record.resolved_path,
                    score_to_anchor=score,
                )
            )
        duplicate_groups.append(
            DuplicateGroup(
                group_id=len(duplicate_groups) + 1,
                kind="exact_or_visual",
                score=group_score,
                items=items,
            )
        )

    return sorted(
        duplicate_groups,
        key=lambda group: (len(group.items), group.score),
        reverse=True,
    )


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
