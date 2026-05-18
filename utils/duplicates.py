import sqlite3
import torch
import numpy as np
from sentence_transformers import util
import os


DB_FILE = "meme_compass_v9.db"
SIMILARITY_THRESHOLD = 0.985  

def find_duplicates():
    if not os.path.exists(DB_FILE):
        print("Banco de dados não encontrado.")
        return

    print("Carregando embeddings do banco de dados...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT id, arquivo, caminho, embedding FROM memes')
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("Banco vazio.")
        return

    
    ids = [r[0] for r in rows]
    filenames = [r[1] for r in rows]
    paths = [r[2] for r in rows]
    embeddings = torch.tensor(np.array([np.frombuffer(r[3], dtype=np.float32) for r in rows]))

    print(f"Analisando {len(embeddings)} arquivos em busca de duplicatas...")
    
    
    
    cos_scores = util.cos_sim(embeddings, embeddings)

    
    mask = torch.triu(torch.ones_like(cos_scores), diagonal=1).bool()
    duplicates = torch.nonzero((cos_scores > SIMILARITY_THRESHOLD) & mask)

    if len(duplicates) == 0:
        print("Nenhuma duplicata encontrada!")
        return

    print(f"\nEncontrados {len(duplicates)} pares de duplicatas potenciais:\n")
    
    for i, j in duplicates:
        idx1, idx2 = i.item(), j.item()
        score = cos_scores[idx1][idx2].item()
        print(f"[{score:.4f}] Duplicata encontrada:")
        print(f"  1: {filenames[idx1]}")
        print(f"  2: {filenames[idx2]}\n")

if __name__ == "__main__":
    find_duplicates()