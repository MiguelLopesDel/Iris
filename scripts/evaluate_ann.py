#!/usr/bin/env python3
"""Decompose where an approximate vector index loses information.

Search is exhaustive today: every query compares against every stored embedding,
which is exact and resident by definition. At 768 dimensions that is 3 KB per
item before anything else, so an exact index cannot be the answer at scale. The
question is not *which index looks good* but **where each approximation loses
what**, so the configurations below are ordered to isolate one source of error
at a time:

    Flat fp32        the oracle: exhaustive, exact, what every other row is
                     measured against
    Flat SQfp16      error from reducing precision alone; still exhaustive
    IVF-Flat fp32    error from not visiting every cell; no compression
    IVF-PQ           the above plus quantisation error
    OPQ + IVF-PQ     how much a rotation before quantising recovers
    IVF-PQ + refine  the candidate architecture: approximate generation,
                     precise reordering

Measuring only the final ranking would blend three different failures into one
number, which is the mistake this whole exercise exists to avoid.

**Candidate recall is the metric that decides.** A generator does not need to
rank well; it needs to not lose what the reranker will need. If PQ places only
91% of the oracle's top ten in its own top ten but 99.8% of them somewhere in
its top hundred, then reranking recovers the ranking and the compression is
free.

Query sets are separated because they are different distributions, and a hard
set is included on purpose: over a catalogue where neighbours sit at 0.94, 0.82
and 0.71, any index looks excellent. The queries that discriminate are the ones
whose neighbours are tied around 0.812.

    python scripts/evaluate_ann.py --db data/iris_v1.db --queries 200
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import faiss
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TOP_K = 10
CANDIDATE_K = 100


@dataclass
class Result:
    name: str
    bytes_per_vector: float
    build_seconds: float
    query_ms: float
    recall_at_k: dict[str, float] = field(default_factory=dict)
    candidate_recall: dict[str, float] = field(default_factory=dict)


def load_embeddings(db_path: Path, limit: int | None) -> tuple[np.ndarray, list[int]]:
    """Valid embeddings only, with the ids they belong to.

    Items without an embedding never enter: a placeholder row would be training
    data for the coarse quantiser, shifting the very centroids it is trying to
    learn.
    """
    conn = sqlite3.connect(db_path)
    sql = "SELECT id, embedding FROM memes WHERE embedding IS NOT NULL ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    ids: list[int] = []
    vectors: list[np.ndarray] = []
    width = None
    for media_id, blob in conn.execute(sql):
        vector = np.frombuffer(blob, dtype=np.float32)
        if width is None:
            width = vector.shape[0]
        if vector.shape[0] != width:
            continue
        ids.append(int(media_id))
        vectors.append(vector)
    conn.close()
    matrix = np.vstack(vectors).astype(np.float32)
    faiss.normalize_L2(matrix)
    return matrix, ids


def build_queries(
    matrix: np.ndarray, count: int, seed: int, model_name: str | None
) -> dict[str, np.ndarray]:
    """Four distributions, because they stress the index differently."""
    rng = np.random.default_rng(seed)
    sets: dict[str, np.ndarray] = {}

    # An item searching for items like itself: the "similar media" path.
    picked = rng.choice(len(matrix), size=min(count, len(matrix)), replace=False)
    sets["image"] = matrix[picked].copy()

    # A concept centroid: several references averaged, as the concepts tab does.
    centroids = []
    for _ in range(min(count, len(matrix))):
        members = rng.choice(len(matrix), size=min(5, len(matrix)), replace=False)
        centroid = matrix[members].mean(axis=0)
        centroids.append(centroid / max(np.linalg.norm(centroid), 1e-12))
    sets["concept"] = np.vstack(centroids).astype(np.float32)

    if model_name:
        sets["text"] = _text_queries(count, model_name, matrix.shape[1])
    return sets


def _text_queries(count: int, model_name: str, width: int) -> np.ndarray:
    from core.embedding_models import load_encoder

    phrases = [
        "a photo of a person", "a screenshot of a conversation", "a cat",
        "a landscape at sunset", "text on a white background", "a meme with text",
        "a dog running", "food on a plate", "a city street at night",
        "a handwritten note", "a chart or graph", "a car", "a group of people",
        "a drawing or illustration", "a video game screenshot", "a document scan",
    ]
    encoder = load_encoder(model_name, device="cpu")
    vectors = encoder.encode(
        [phrases[i % len(phrases)] for i in range(count)], show_progress_bar=False
    )
    matrix = np.asarray(vectors, dtype=np.float32).reshape(-1, width)
    faiss.normalize_L2(matrix)
    return matrix


def hard_subset(oracle_scores: np.ndarray, queries: np.ndarray, keep: int) -> np.ndarray:
    """Queries whose top neighbours are nearly tied.

    Where the gap between the first and the tenth result is wide, any ordering
    survives compression. The discriminating queries are the crowded ones.
    """
    spread = oracle_scores[:, 0] - oracle_scores[:, TOP_K - 1]
    tightest = np.argsort(spread)[:keep]
    return queries[tightest]


def recall(truth: np.ndarray, found: np.ndarray, depth: int) -> float:
    hits = 0
    for expected, actual in zip(truth, found, strict=True):
        hits += len(set(expected[:TOP_K]) & set(actual[:depth]))
    return hits / (len(truth) * TOP_K)


def _measure(index, queries: dict[str, np.ndarray], truth: dict[str, np.ndarray]) -> tuple:
    recalls, candidates, elapsed = {}, {}, []
    for name, batch in queries.items():
        start = time.time()
        _, found = index.search(batch, CANDIDATE_K)
        elapsed.append((time.time() - start) / len(batch) * 1000)
        recalls[name] = recall(truth[name], found, TOP_K)
        candidates[name] = recall(truth[name], found, CANDIDATE_K)
    return recalls, candidates, float(np.mean(elapsed))


def _set_nprobe(index, nprobe: int) -> None:
    """Reach the IVF inside whatever wrapper it came in."""
    try:
        faiss.extract_index_ivf(index).nprobe = nprobe
    except RuntimeError:
        pass  # not an IVF index at all (Flat, SQ)


def _training_sample(matrix: np.ndarray, cap: int = 50_000) -> np.ndarray:
    """Vectors used to train a quantiser. Sampling keeps training from dominating."""
    if len(matrix) <= cap:
        return matrix
    rng = np.random.default_rng(0)
    return matrix[rng.choice(len(matrix), size=cap, replace=False)]


def evaluate(matrix: np.ndarray, queries: dict[str, np.ndarray], nlist: int, nprobe: int):
    width = matrix.shape[1]
    results: list[Result] = []

    start = time.time()
    oracle = faiss.IndexFlatIP(width)
    oracle.add(matrix)
    oracle_build = time.time() - start
    truth: dict[str, np.ndarray] = {}
    oracle_scores: dict[str, np.ndarray] = {}
    timings = []
    for name, batch in queries.items():
        begin = time.time()
        scores, found = oracle.search(batch, CANDIDATE_K)
        timings.append((time.time() - begin) / len(batch) * 1000)
        truth[name] = found
        oracle_scores[name] = scores
    oracle_result = Result(
        name="Flat fp32 (oracle)",
        bytes_per_vector=4 * width,
        build_seconds=oracle_build,
        query_ms=float(np.mean(timings)),
    )
    results.append(oracle_result)

    # A hard set derived from the oracle: the queries whose neighbours are tied.
    hard = {
        name: hard_subset(oracle_scores[name], batch, max(len(batch) // 4, 1))
        for name, batch in queries.items()
    }
    hard_truth = {}
    for name, batch in hard.items():
        _, found = oracle.search(batch, CANDIDATE_K)
        hard_truth[name] = found
    queries = {**queries, **{f"{name}-hard": batch for name, batch in hard.items()}}
    truth = {**truth, **{f"{name}-hard": found for name, found in hard_truth.items()}}
    # Filled only now: the hard sets did not exist when the oracle ran, and
    # marking it 100% per key beforehand left those columns reading 0.0% -- a
    # number that cannot be true of an index measured against itself.
    oracle_result.recall_at_k = {name: 1.0 for name in queries}
    oracle_result.candidate_recall = {name: 1.0 for name in queries}

    def register(name: str, index, bytes_per_vector: float, trained=False):
        print(f"  … {name}", flush=True)
        start = time.time()
        if not trained:
            # OPQ learns a rotation of a 768x768 matrix; training it on the whole
            # catalogue costs far more than it buys, and FAISS samples anyway.
            for component in (index, getattr(index, "index", None)):
                if hasattr(component, "cp"):
                    component.cp.max_points_per_centroid = 64
            index.train(_training_sample(matrix))
        index.add(matrix)
        build = time.time() - start
        # Never `hasattr(index, "nprobe")`. A factory string with OPQ returns an
        # IndexPreTransform, which does not carry the attribute, so that check
        # silently left those rows searching one cell out of 444 while the plain
        # IVF rows searched eight. It read as "OPQ ruins recall" -- 58% against
        # 92% -- and would have thrown out the compression that costs 40 bytes a
        # vector instead of 3072.
        _set_nprobe(index, nprobe)
        recalls, candidates, query_ms = _measure(index, queries, truth)
        results.append(Result(name, bytes_per_vector, build, query_ms, recalls, candidates))

    register(
        "Flat SQfp16",
        faiss.IndexScalarQuantizer(width, faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT),
        2 * width,
    )
    register(
        f"IVF-Flat (nprobe={nprobe})",
        faiss.IndexIVFFlat(faiss.IndexFlatIP(width), width, nlist, faiss.METRIC_INNER_PRODUCT),
        4 * width + 8,
    )
    for m in (32, 64):
        register(
            f"IVF-PQ{m} (nprobe={nprobe})",
            faiss.IndexIVFPQ(faiss.IndexFlatIP(width), width, nlist, m, 8, faiss.METRIC_INNER_PRODUCT),
            m + 8,
        )
        register(
            f"OPQ+IVF-PQ{m} (nprobe={nprobe})",
            faiss.index_factory(width, f"OPQ{m},IVF{nlist},PQ{m}", faiss.METRIC_INNER_PRODUCT),
            m + 8,
        )

    # The candidate architecture. The generator does not have to rank well, only
    # to keep what the reranker needs, so what this measures is whether a more
    # precise second pass over a hundred candidates recovers the oracle ordering
    # the compression threw away.
    for m in (32, 64):
        for refine, extra in (("fp16", 2 * width), ("fp32", 4 * width)):
            base = faiss.index_factory(
                width, f"OPQ{m},IVF{nlist},PQ{m}", faiss.METRIC_INNER_PRODUCT
            )
            # Built rather than parsed: the factory grammar puts Refine at the
            # top level, and writing it after the PQ makes it part of the IVF's
            # own code description, which does not parse.
            if refine == "fp16":
                storage = faiss.IndexScalarQuantizer(
                    width, faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT
                )
            else:
                storage = faiss.IndexFlatIP(width)
            index = faiss.IndexRefine(base, storage)
            # One, deliberately. k_factor multiplies the k the caller asks for,
            # and every row here is searched at CANDIDATE_K, so a factor of ten
            # had the generator fetch a thousand candidates while the rows above
            # it fetched a hundred. The reranked rows then looked better for a
            # reason that had nothing to do with reranking.
            index.k_factor = 1
            register(f"OPQ+IVF-PQ{m} -> rerank {refine}", index, m + 8 + extra)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/iris_v1.db"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--nprobe", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default=None, help="Encoder for text queries; omitted, skips them.")
    args = parser.parse_args()

    matrix, ids = load_embeddings(args.db, args.limit)
    # FAISS trains a coarse quantiser with k-means, and too many cells for too
    # few points learns noise. Its own guidance is between 4 and 16 times the
    # square root of the count.
    # Two constraints, and using only the first is how the first run ended up
    # training 526 centroids on 17k points: FAISS asks for at least 39 training
    # points per centroid, and warns rather than failing, so the damage would
    # have shown up as "PQ compresses too much".
    nlist = max(min(int(4 * np.sqrt(len(matrix))), len(matrix) // 39), 1)
    print(f"{len(matrix):,} embeddings de {matrix.shape[1]} dims | nlist={nlist} nprobe={args.nprobe}\n")

    queries = build_queries(matrix, args.queries, args.seed, args.model)
    results = evaluate(matrix, queries, nlist, args.nprobe)

    names = sorted({name for result in results for name in result.recall_at_k})
    header = f"{'índice':<28} {'B/vec':>7} {'build':>7} {'ms/q':>7}  " + " ".join(
        f"{name[:11]:>11}" for name in names
    )
    print(header)
    print("-" * len(header))
    for result in results:
        row = f"{result.name:<28} {result.bytes_per_vector:>7.0f} {result.build_seconds:>6.1f}s {result.query_ms:>6.2f}ms  "
        row += " ".join(f"{result.recall_at_k.get(name, 0):>10.1%}" for name in names)
        print(row)

    print(f"\ncandidate recall@{CANDIDATE_K} (quanto do top-{TOP_K} do oráculo sobra para o rerank):")
    print(f"{'índice':<28}  " + " ".join(f"{name[:11]:>11}" for name in names))
    for result in results[1:]:
        print(
            f"{result.name:<28}  "
            + " ".join(f"{result.candidate_recall.get(name, 0):>10.1%}" for name in names)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
