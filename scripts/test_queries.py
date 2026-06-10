import sys
import os

# Adiciona a pasta raiz ao path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.search_engine import IrisEngine

def run_tests():
    # Carrega o buscador com o banco de teste e os pesos otimizados
    engine = IrisEngine(db_path="data/teste_playground.db")
    
    # Lista de buscas variadas baseadas no que encontramos no banco
    test_queries = [
        {"tipo": "Literal (OCR)", "termo": "derrotar o Superman"},
        {"tipo": "Contextual (Português)", "termo": "meme sobre calor de noite"},
        {"tipo": "Visual/Estilo", "termo": "captura de tela do youtube"},
        {"tipo": "Abstrato", "termo": "erro de programação código"},
        {"tipo": "Música", "termo": "banda de rock japonesa YOASOBI"}
    ]

    print("🚀 EXECUTANDO TESTES DE BUSCA COM IA OTIMIZADA\n")
    print("="*60)

    for q in test_queries:
        print(f"🔍 Busca [{q['tipo']}]: '{q['termo']}'")
        results = engine.buscar(q['termo'], top_k=2)
        
        if not results:
            print("   ❌ Nenhum resultado encontrado.")
        else:
            for i, res in enumerate(results):
                status = "✅ (Match!)" if i == 0 else "🥈 (Segundo lugar)"
                print(f"   {status} [{res['score']:.4f}] Arquivo: {res['arquivo']}")
        print("-" * 60)

if __name__ == "__main__":
    run_tests()
