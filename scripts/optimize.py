from __future__ import annotations

import argparse
import json
from pathlib import Path

from _path import ensure_project_root
from tqdm import tqdm

ensure_project_root()

from core.search_engine import IrisEngine  # noqa: E402


def simplify_query(text: str) -> str:
    query = text.lower()
    for phrase in ["a photo of", "close up of", "image of", "showing", "background"]:
        query = query.replace(phrase, " ")
    return " ".join(query.split()[:8])


def evaluate(engine: IrisEngine, weights: dict[str, float]) -> float:
    hits = 0
    total = len(engine.dados)
    for sample in engine.dados:
        query = f"{sample.get('tags', '')} {simplify_query(sample['descricao_ia'])}".strip()
        results = engine.buscar(query, top_k=1, translate=False, custom_weights=weights)
        if results and results[0]["arquivo"] == sample["arquivo"]:
            hits += 1
    return hits / total if total else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default="data/teste_playground.db")
    parser.add_argument("--output", type=str, default="data/best_weights.json")
    args = parser.parse_args()

    engine = IrisEngine(db_path=args.db)
    if not engine.dados:
        print(f"Banco nao encontrado ou vazio: {args.db}")
        return

    balances = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    text_bonuses = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    best_score = -1.0
    best_weights = {"balance": 0.5, "text_bonus": 2.0}

    print(f"Testando {len(balances) * len(text_bonuses)} combinacoes...")
    for balance in tqdm(balances):
        for text_bonus in text_bonuses:
            weights = {"balance": balance, "text_bonus": text_bonus}
            score = evaluate(engine, weights)
            if score > best_score:
                best_score = score
                best_weights = weights

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(best_weights, indent=2), encoding="utf-8")
    print(f"Melhor Top 1: {best_score * 100:.1f}%")
    print(f"Pesos salvos em {output}: {best_weights}")


if __name__ == "__main__":
    main()
