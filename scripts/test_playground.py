import os
import sys
import shutil
import random
import subprocess
import argparse

def setup_test_env(src_dir, test_dir, num_images):
    if os.path.exists(test_dir):
        print(f"Limpando diretório de teste anterior: {test_dir}...")
        shutil.rmtree(test_dir)
    os.makedirs(test_dir)
    
    if not os.path.exists(src_dir):
        print(f"❌ Erro: Diretório fonte '{src_dir}' não encontrado.")
        return False

    all_files = [f for f in os.listdir(src_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4', '.mkv', '.webm'))]
    if not all_files:
        print(f"❌ Nenhuma mídia encontrada em {src_dir}")
        return False
        
    selected_files = random.sample(all_files, min(num_images, len(all_files)))
    
    print(f"📂 Copiando {len(selected_files)} arquivos aleatórios para o ambiente de teste ({test_dir})...")
    for f in selected_files:
        shutil.copy(os.path.join(src_dir, f), os.path.join(test_dir, f))
        
    return True

def run_indexer(test_dir, db_name):
    print(f"\n🧠 Executando indexador para os arquivos de teste (isso deve demorar apenas alguns segundos/minutos)...")
    result = subprocess.run([sys.executable, "core/indexer.py", "--dir", test_dir, "--db", db_name])
    return result.returncode == 0

def run_cli_search(db_name):
    print("\n" + "="*60)
    print("🚀 AMBIENTE DE TESTE PRONTO!")
    print("="*60)
    print(f"Foram indexadas algumas imagens aleatórias no banco: {db_name}")
    print("\nPara testar a nova inteligência de busca sem esperar 1 hora:")
    print("1. Inicie a interface:")
    print(f"   IRIS_DB={db_name} ./scripts/run_app.sh")
    print("2. Abra http://localhost:8501 no navegador.")
    print("\nAgora você pode fazer suas buscas (em Português!) e validar se a IA")
    print("está entendendo melhor o contexto antes de rodar o indexador na")
    print("sua pasta completa de imagens.")
    print("="*60 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Cria um ambiente de teste rápido para validar a busca.")
    parser.add_argument("--src", type=str, default="media", help="Pasta original com todas as imagens")
    parser.add_argument("--num", type=int, default=15, help="Quantidade de imagens aleatórias para testar")
    parser.add_argument("--test-dir", type=str, default="tmp_test_images", help="Pasta temporária de teste")
    parser.add_argument("--test-db", type=str, default="teste_playground.db", help="Banco de dados de teste")
    
    args = parser.parse_args()
    
    print("--- 🧪 PLAYGROUND DE TESTE DO IRIS ---")
    if setup_test_env(args.src, args.test_dir, args.num):
        if run_indexer(args.test_dir, args.test_db):
            run_cli_search(args.test_db)
        else:
            print("❌ Erro ao indexar as imagens de teste.")
    else:
        print("❌ Erro ao preparar o ambiente de teste.")

if __name__ == "__main__":
    main()
