from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from _path import ensure_project_root

ensure_project_root()

from core.search_engine import MemeSearchEngine  # noqa: E402


def result_rank(results: list[dict], target_file: str) -> int:
    for rank, result in enumerate(results, start=1):
        if result["arquivo"] == target_file:
            return rank
    return -1


def run_benchmark(db_path: str, num_tests: int = 20, seed: int = 42) -> dict[str, float]:
    print(f"Iniciando benchmark no banco: {db_path}")
    engine = MemeSearchEngine(db_path=db_path)
    if not engine.dados:
        print("Erro: banco vazio ou inexistente.")
        return {"top1": 0.0, "top3": 0.0, "top5": 0.0, "latency_ms": 0.0}

    rng = random.Random(seed)
    samples = rng.sample(engine.dados, min(num_tests, len(engine.dados)))
    hits_top1 = hits_top3 = hits_top5 = 0
    elapsed = 0.0

    for index, sample in enumerate(samples, start=1):
        query = f"{sample.get('tags', '')} {sample['descricao_ia']}".strip()
        target_file = sample["arquivo"]
        start_time = time.perf_counter()
        results = engine.buscar(query, top_k=5, translate=False)
        elapsed += time.perf_counter() - start_time

        found_at = result_rank(results, target_file)
        if found_at == 1:
            hits_top1 += 1
        if 1 <= found_at <= 3:
            hits_top3 += 1
        if 1 <= found_at <= 5:
            hits_top5 += 1

        status = "OK" if 1 <= found_at <= 3 else "MISS"
        print(
            f"Teste {index}/{len(samples)}: {status} arquivo={target_file} "
            f"rank={found_at if found_at != -1 else 'N/A'}"
        )

    total = len(samples)
    metrics = {
        "top1": hits_top1 / total,
        "top3": hits_top3 / total,
        "top5": hits_top5 / total,
        "latency_ms": (elapsed / total) * 1000,
    }
    print("\nRESULTADO DO BENCHMARK")
    print(f"Top 1: {metrics['top1'] * 100:.1f}%")
    print(f"Top 3: {metrics['top3'] * 100:.1f}%")
    print(f"Top 5: {metrics['top5'] * 100:.1f}%")
    print(f"Latencia media: {metrics['latency_ms']:.1f} ms")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default="data/teste_playground.db")
    parser.add_argument("--num", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not Path(args.db).exists():
        print(f"Banco nao encontrado: {args.db}")
        return
    run_benchmark(args.db, args.num, args.seed)


if __name__ == "__main__":
    main()
