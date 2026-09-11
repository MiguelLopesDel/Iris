#!/usr/bin/env python3
"""Attribute the loss: partition, rotation, quantisation, refinement.

The previous round compared ``IVF-Flat`` against ``OPQ+IVF-PQ`` and credited the
difference to the codec. That comparison cannot support the claim: OPQ is a
rotation applied *before* the coarse quantiser as well, so the two rows do not
share a partition, and part of any difference is the cells being drawn
differently rather than the vectors being compressed differently.

Four configurations over one fixed geometry close that gap:

    IVF-Flat            the partition, uncompressed
    IVF-PQ              quantisation cost in the original space
    OPQ+IVF-Flat        the rotation's effect on the partition, uncompressed
    OPQ+IVF-PQ          quantisation cost in the rotated space

Which makes each term readable as a subtraction rather than a guess:

    IVF-Flat      - IVF-PQ           = quantisation, no rotation
    OPQ+IVF-Flat  - OPQ+IVF-PQ       = quantisation, rotated
    IVF-Flat      - OPQ+IVF-Flat     = the rotation alone
    OPQ+IVF-PQ    - ...+refine       = what reordering recovers

Query classes are named for what they are. Averaging five unrelated vectors
produces a point in a region no real concept occupies: a useful stress test, and
a misleading thing to call "concept", because tuning the index to satisfy a
distribution that production never issues costs real I/O for nothing. The real
concepts stored in the catalogue are measured separately.

    python scripts/compare_ivf_codecs.py --db data/iris_v1.db --nlist 444 --nprobe 32
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import faiss
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.evaluate_ann import (  # noqa: E402
    CANDIDATE_K,
    TOP_K,
    _training_sample,
    hard_subset,
    load_embeddings,
    recall,
)
from scripts.sweep_ivf import set_nprobe  # noqa: E402


def real_concept_queries(db_path: Path, ids: list[int], matrix: np.ndarray) -> np.ndarray | None:
    """Centroids of the concepts a person actually created in this catalogue."""
    position = {media_id: index for index, media_id in enumerate(ids)}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT concept_id, meme_id FROM concept_media WHERE confirmed = 1"
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()

    members: dict[int, list[int]] = {}
    for concept_id, media_id in rows:
        index = position.get(int(media_id))
        if index is not None:
            members.setdefault(int(concept_id), []).append(index)

    centroids = []
    for indices in members.values():
        if len(indices) < 2:
            continue
        centroid = matrix[indices].mean(axis=0)
        centroids.append(centroid / max(np.linalg.norm(centroid), 1e-12))
    return np.vstack(centroids).astype(np.float32) if centroids else None


def build_query_sets(
    matrix: np.ndarray, ids: list[int], db_path: Path, count: int, seed: int
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    sets: dict[str, np.ndarray] = {}

    picked = rng.choice(len(matrix), size=min(count, len(matrix)), replace=False)
    sets["image"] = matrix[picked].copy()

    # Named for what it is: five unrelated vectors averaged land off the manifold
    # the catalogue occupies. Kept because it is hard, not because it is typical.
    centroids = []
    for _ in range(min(count, len(matrix))):
        members = rng.choice(len(matrix), size=5, replace=False)
        centroid = matrix[members].mean(axis=0)
        centroids.append(centroid / max(np.linalg.norm(centroid), 1e-12))
    sets["synthetic"] = np.vstack(centroids).astype(np.float32)

    real = real_concept_queries(db_path, ids, matrix)
    if real is not None:
        sets["real-concept"] = real
    return sets


def evaluate_index(index, queries, truth) -> tuple[dict[str, float], dict[str, float], float, float]:
    recalls, candidates, elapsed, scanned = {}, {}, [], []
    for name, batch in queries.items():
        faiss.cvar.indexIVF_stats.reset()
        start = time.time()
        _, found = index.search(batch, CANDIDATE_K)
        elapsed.append((time.time() - start) / len(batch) * 1000)
        scanned.append(faiss.cvar.indexIVF_stats.ndis / len(batch))
        recalls[name] = recall(truth[name], found, TOP_K)
        candidates[name] = recall(truth[name], found, CANDIDATE_K)
    return recalls, candidates, float(np.mean(elapsed)), float(np.mean(scanned))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/iris_v1.db"))
    parser.add_argument("--queries", type=int, default=120)
    parser.add_argument("--nlist", type=int, default=444)
    parser.add_argument("--nprobe", type=int, default=32)
    parser.add_argument("--m", type=int, default=64)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    matrix, ids = load_embeddings(args.db, None)
    width = matrix.shape[1]
    queries = build_query_sets(matrix, ids, args.db, args.queries, args.seed)

    oracle = faiss.IndexFlatIP(width)
    oracle.add(matrix)
    truth, scores = {}, {}
    for name, batch in queries.items():
        found_scores, found = oracle.search(batch, CANDIDATE_K)
        truth[name] = found
        scores[name] = found_scores
    for name in list(queries):
        hard = hard_subset(scores[name], queries[name], max(len(queries[name]) // 4, 1))
        _, hard_truth = oracle.search(hard, CANDIDATE_K)
        queries[f"{name}-hard"] = hard
        truth[f"{name}-hard"] = hard_truth

    print(
        f"{len(matrix):,} vetores | nlist={args.nlist} nprobe={args.nprobe} m={args.m}"
        f" | classes: {', '.join(sorted(queries))}\n"
    )

    sample = _training_sample(matrix)
    names = sorted(queries)
    rows = []
    header = f"{'configuração':<22} {'B/vec':>6} {'build':>7} {'ms/q':>7} {'ndis':>7}  " + "  ".join(
        f"{name[:15]:>15}" for name in names
    )
    print("recall@10 contra o oráculo exato (candidate@100 entre parênteses)")
    print(header)
    print("-" * len(header), flush=True)

    def report(name, size, build, ms, ndis, recalls, candidates):
        # Printed as it is produced. Accumulating until the end meant a failure
        # in the last configuration threw away every measurement before it --
        # which is exactly what happened, twice.
        print(
            f"{name:<22} {size:>6} {build:>6.0f}s {ms:>6.2f}ms {ndis:>7.0f}  "
            + "  ".join(f"{recalls[key]:>14.1%}" for key in names),
            flush=True,
        )
        print(
            f"{'':<22} {'':>6} {'':>7} {'':>7} {'':>7}  "
            + "  ".join(f"{'(' + format(candidates[key], '.1%') + ')':>14}" for key in names),
            flush=True,
        )

    def run(name: str, index, bytes_per_vector: int):
        print(f"  … {name}", flush=True)
        start = time.time()
        index.train(sample)
        index.add(matrix)
        build = time.time() - start
        set_nprobe(index, args.nprobe)
        recalls, candidates, ms, ndis = evaluate_index(index, queries, truth)
        rows.append((name, bytes_per_vector, build, ms, ndis, recalls, candidates))
        report(name, bytes_per_vector, build, ms, ndis, recalls, candidates)
        return index

    run(
        "IVF-Flat",
        faiss.IndexIVFFlat(faiss.IndexFlatIP(width), width, args.nlist, faiss.METRIC_INNER_PRODUCT),
        4 * width,
    )
    run(
        f"IVF-PQ{args.m}",
        faiss.IndexIVFPQ(
            faiss.IndexFlatIP(width), width, args.nlist, args.m, 8, faiss.METRIC_INNER_PRODUCT
        ),
        args.m + 8,
    )
    run(
        "OPQ+IVF-Flat",
        faiss.index_factory(
            width, f"OPQ{args.m},IVF{args.nlist},Flat", faiss.METRIC_INNER_PRODUCT
        ),
        4 * width,
    )
    base = run(
        f"OPQ+IVF-PQ{args.m}",
        faiss.index_factory(
            width, f"OPQ{args.m},IVF{args.nlist},PQ{args.m}", faiss.METRIC_INNER_PRODUCT
        ),
        args.m + 8,
    )

    for label, storage, extra in (
        ("fp16", faiss.IndexScalarQuantizer(width, faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT), 2 * width),
        ("fp32", faiss.IndexFlatIP(width), 4 * width),
    ):
        print(f"  … rerank {label}", flush=True)
        # Populated before wrapping: IndexRefine checks at construction that both
        # indexes already hold the same number of vectors.
        start = time.time()
        storage.train(sample)
        storage.add(matrix)
        build = time.time() - start
        refined = faiss.IndexRefine(base, storage)
        refined.k_factor = 1
        set_nprobe(refined, args.nprobe)
        recalls, candidates, ms, ndis = evaluate_index(refined, queries, truth)
        rows.append(
            (f"  -> rerank {label}", args.m + 8 + extra, build, ms, ndis, recalls, candidates)
        )
        report(f"  -> rerank {label}", args.m + 8 + extra, build, ms, ndis, recalls, candidates)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
