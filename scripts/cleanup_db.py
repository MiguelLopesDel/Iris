import os
import sqlite3


IMAGE_DIR = './minhas_imagens'
DB_FILE = "iris.db" 

def cleanup_database():
    """
    Verifica o banco de dados e remove entradas de imagens que não existem mais no disco.
    """
    if not os.path.exists(DB_FILE):
        print(f"Erro: Arquivo de banco de dados '{DB_FILE}' não encontrado.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    
    cursor.execute('SELECT id, caminho FROM memes')
    db_files = cursor.fetchall()
    if not db_files:
        print("Banco de dados está vazio. Nada a fazer.")
        conn.close()
        return
        
    print(f"Verificando {len(db_files)} registros no banco de dados...")

    
    orphaned_ids = [(record_id,) for record_id, file_path in db_files if not os.path.exists(file_path)]

    
    if orphaned_ids:
        print(f"Encontrados {len(orphaned_ids)} registros órfãos. Removendo...")
        cursor.executemany('DELETE FROM memes WHERE id = ?', orphaned_ids)
        conn.commit()
        print("Limpeza concluída.")
    else:
        print("Nenhum registro órfão encontrado. O banco de dados está sincronizado.")

    conn.close()

if __name__ == "__main__":
    cleanup_database()