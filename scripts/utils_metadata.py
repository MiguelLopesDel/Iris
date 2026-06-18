import os
import sqlite3

import piexif
from tqdm import tqdm

DB_FILE = "iris.db"

def sync_metadata():
    """
    Lê as descrições do banco de dados e as escreve nos metadados EXIF (UserComment) das imagens JPG.
    Isso permite que o Windows/Linux busquem as imagens nativamente.
    """
    if not os.path.exists(DB_FILE):
        print("Banco de dados não encontrado.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    
    cursor.execute('SELECT caminho, descricao_ia, texto_extraido FROM memes WHERE descricao_ia IS NOT NULL')
    rows = cursor.fetchall()
    conn.close()

    print(f"Sincronizando metadados de {len(rows)} imagens...")

    for caminho, descricao, ocr in tqdm(rows):
        if not os.path.exists(caminho):
            continue
            
        
        if not caminho.lower().endswith(('.jpg', '.jpeg')):
            continue

        try:
            
            full_text = f"IA: {descricao} | OCR: {ocr}"
            
            
            try:
                exif_dict = piexif.load(caminho)
            except Exception:
                exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

            
            
            user_comment = piexif.helper.UserComment.dump(full_text, encoding="unicode")
            exif_dict["Exif"][piexif.ExifIFD.UserComment] = user_comment

            exif_bytes = piexif.dump(exif_dict)
            piexif.insert(exif_bytes, caminho)
            
        except Exception:
            
            pass

    print("Sincronização concluída! Agora você pode buscar suas imagens pelo Windows Explorer.")

if __name__ == "__main__":
    sync_metadata()