#!/usr/bin/env python3
"""Does a smaller code with a rotation match a larger one, once reranked?

The candidate architecture is settled in shape -- compress, retrieve a hundred,
reorder precisely -- so the remaining question is which code size to pay for.
``PQ32`` and ``PQ64`` differ by 32 bytes per vector, which is 3.2 GB across the
hundred million vectors a fifty-million-item library holds in two modalities.
On the 8 GB machine this project targets, that is an architectural decision
rather than a tuning detail.

OPQ looked not worth having when measured against ``PQ64``, where it bought 0.3
of a point. That was the wrong regime to judge it in: it exists to reduce the
distortion of splitting a space into subspaces, and that distortion grows as
each subspace gets fewer bits. Against ``PQ32`` it recovers two to four points.

**Trained once per configuration.** The earlier run rebuilt every index for each
query seed, but a seed varies the queries, not the training sample, and FAISS
trains deterministically from the same input -- so three identical rotations
were learned at roughly ten minutes each. Query variation and training variation
are separate axes and only the first is measured here; calling both "seed" is
what hid the waste.

    python scripts/compare_operating_points.py --db data/iris_v1.db
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import faiss
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.compare_ivf_codecs import build_query_sets  # noqa: E402
from scripts.evaluate_ann import (  # noqa: E402
    CANDIDATE_K,
    TOP_K,
    _training_sample,
    load_embeddings,
    recall,
)
from scripts.sweep_ivf import set_nprobe  # noqa: E402

CLASSES = ("image", "real-concept", "synthetic")


def describe_environment(sample_size: int) -> None:
    """State what is actually running rather than what is assumed to run."""
    print(f"faiss {faiss.__version__} | OMP threads {faiss.omp_get_max_threads()}")
    print(f"training sample: {sample_size:,} vetores")
    probe = faiss.OPQMatrix(8, 2)
    details = [f"{name}={getattr(probe, name)}" for name in ("niter", "niter_pq") if hasattr(probe, name)]
    clustering = faiss.ClusteringParameters()
    details.append(f"kmeans niter={clustering.niter}")
    details.append(f"max_points_per_centroid={clustering.max_points_per_centroid}")
    print("OPQ/clustering: " + ", ".join(details) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/iris_v1.db"))
    parser.add_argument("--queries", type=int, default=120)
    parser.add_argument("--nlist", type=int, default=444)
    parser.add_argument("--nprobe", type=int, default=32)
    parser.add_argument("--query-seeds", type=int, nargs="+", default=[1, 2, 3])
    args = parser.parse_args()

    matrix, ids = load_embeddings(args.db, None)
    width = matrix.shape[1]
    sample = _training_sample(matrix)
    describe_environment(len(sample))

    # Query sets and ground truth, built once: the oracle does not depend on
    # which codec is being judged.
    oracle = faiss.IndexFlatIP(width)
    oracle.add(matrix)
    query_sets, truths = {}, {}
    for seed in args.query_seeds:
        batch = build_query_sets(matrix, ids, args.db, args.queries, seed)
        query_sets[seed] = {name: batch[name] for name in CLASSES if name in batch}
        truths[seed] = {
            name: oracle.search(vectors, CANDIDATE_K)[1]
            for name, vectors in query_sets[seed].items()
        }

    # The refinement storage is shared: it is the same fp16 copy of the same
    # vectors regardless of which generator hands it candidates.
    refine_storage = faiss.IndexScalarQuantizer(
        width, faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT
    )
    refine_storage.train(sample)
    refine_storage.add(matrix)

    configurations = [
        ("IVF-PQ32", f"IVF{args.nlist},PQ32", 40),
        ("OPQ+IVF-PQ32", f"OPQ32,IVF{args.nlist},PQ32", 40),
        ("IVF-PQ64", f"IVF{args.nlist},PQ64", 72),
    ]

    header = (
        f"{'configuração':<16} {'B/vec':>6} {'treino':>8} {'etapa':<10} "
        + " ".join(f"{name[:12]:>13}" for name in CLASSES)
    )
    print(header)
    print("-" * len(header), flush=True)

    for label, factory, size in configurations:
        index = faiss.index_factory(width, factory, faiss.METRIC_INNER_PRODUCT)
        start = time.time()
        index.train(sample)
        index.add(matrix)
        build = time.time() - start
        set_nprobe(index, args.nprobe)

        reranked = faiss.IndexRefine(index, refine_storage)
        reranked.k_factor = 1
        set_nprobe(reranked, args.nprobe)

        # Averaged over query seeds, with the spread shown, so a difference
        # smaller than the variation between query sets cannot be read as a
        # result.
        candidate, final = {name: [] for name in CLASSES}, {name: [] for name in CLASSES}
        for seed in args.query_seeds:
            for name, vectors in query_sets[seed].items():
                _, found = index.search(vectors, CANDIDATE_K)
                candidate[name].append(recall(truths[seed][name], found, CANDIDATE_K))
                _, refined = reranked.search(vectors, CANDIDATE_K)
                final[name].append(recall(truths[seed][name], refined, TOP_K))

        for stage, values in (("candidato", candidate), ("+rerank", final)):
            cells = []
            for name in CLASSES:
                series = np.array(values[name])
                cells.append(f"{series.mean():>7.1%}±{series.ptp() / 2:>4.1%}")
            prefix = f"{label:<16} {size:>6} {build:>7.0f}s" if stage == "candidato" else f"{'':<16} {'':>6} {'':>8}"
            print(f"{prefix} {stage:<10} " + " ".join(cells), flush=True)
        print(flush=True)

    print(
        f"candidato = candidate recall@{CANDIDATE_K}; +rerank = recall@{TOP_K} final após"
        f" reordenar com fp16. Média ± meia-amplitude sobre {len(args.query_seeds)} conjuntos de consulta."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
