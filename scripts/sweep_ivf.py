#!/usr/bin/env python3
"""Choose the coarse geometry before arguing about compression.

The error decomposition put the dominant loss in candidate selection, not in
quantisation: an IVF over uncompressed fp32 vectors already lost nearly half the
oracle's neighbours on centroid queries. Tuning the codec before fixing that
would have been optimising the smaller term.

So this sweeps only ``nlist`` and ``nprobe``, over ``IVF-Flat`` with no codec at
all, which makes every number here attributable to one thing: how much of the
space the search actually visits.

**Reported as vectors scanned, not as nprobe.** ``nprobe=8`` means different
work in different indexes -- the fraction of the catalogue visited is roughly
``nprobe/nlist`` only when the lists are evenly filled, and they never are.
FAISS counts the distances it really computed, and that number is what survives
a change of scale: 39 vectors per list here against roughly 190 in a
50M-item index means the same nprobe buys entirely different coverage.

    python scripts/sweep_ivf.py --db data/iris_v1.db
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import faiss
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.evaluate_ann import (  # noqa: E402
    CANDIDATE_K,
    TOP_K,
    build_queries,
    hard_subset,
    load_embeddings,
    recall,
)


def set_nprobe(index, nprobe: int) -> None:
    """Reach the IVF whatever it is wrapped in.

    ``hasattr(index, "nprobe")`` is the trap: a factory string with OPQ returns
    an IndexPreTransform, which does not carry the attribute, so the assignment
    is skipped in silence and the index searches one cell.
    """
    faiss.ParameterSpace().set_index_parameter(index, "nprobe", nprobe)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/iris_v1.db"))
    parser.add_argument("--queries", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--nlist", type=int, nargs="+", default=[128, 256, 444])
    parser.add_argument("--nprobe", type=int, nargs="+", default=[1, 4, 8, 16, 32, 64])
    args = parser.parse_args()

    matrix, _ = load_embeddings(args.db, None)
    width = matrix.shape[1]
    queries = build_queries(matrix, args.queries, args.seed, None)

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

    print(f"{len(matrix):,} vetores de {width} dims | oráculo exato como referência\n")
    names = sorted(queries)
    header = (
        f"{'nlist':>6} {'nprobe':>7} {'vet/lista':>10} {'ndis/q':>9} {'%acervo':>8} {'ms/q':>7}  "
        + "  ".join(f"{name[:16]:>16}" for name in names)
    )
    print(header)
    print("-" * len(header))

    for nlist in args.nlist:
        index = faiss.IndexIVFFlat(
            faiss.IndexFlatIP(width), width, nlist, faiss.METRIC_INNER_PRODUCT
        )
        index.train(matrix)
        index.add(matrix)
        per_list = len(matrix) / nlist
        for nprobe in args.nprobe:
            if nprobe > nlist:
                continue
            set_nprobe(index, nprobe)
            candidate_recalls, scanned, elapsed = {}, [], []
            for name, batch in queries.items():
                faiss.cvar.indexIVF_stats.reset()
                start = time.time()
                _, found = index.search(batch, CANDIDATE_K)
                elapsed.append((time.time() - start) / len(batch) * 1000)
                # Distances actually computed, which is the honest measure of
                # how much of the catalogue this configuration looked at.
                scanned.append(faiss.cvar.indexIVF_stats.ndis / len(batch))
                candidate_recalls[name] = recall(truth[name], found, CANDIDATE_K)
            ndis = float(np.mean(scanned))
            row = (
                f"{nlist:>6} {nprobe:>7} {per_list:>10.0f} {ndis:>9.0f}"
                f" {ndis / len(matrix):>7.1%} {float(np.mean(elapsed)):>6.2f}ms  "
            )
            row += "  ".join(f"{candidate_recalls[name]:>15.1%}" for name in names)
            print(row, flush=True)
        print()
    print(f"recall é candidate@{CANDIDATE_K}: quanto do top-{TOP_K} do oráculo sobra para um rerank.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
