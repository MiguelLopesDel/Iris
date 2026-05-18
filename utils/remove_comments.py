import os
import tokenize
from io import BytesIO

def remove_comments_and_docstrings(source_code):
    """
    Remove comentários e docstrings de um código Python usando o tokenizador oficial.
    Isso é muito mais seguro que Regex, pois entende o contexto (ex: '#' dentro de string).
    """
    io_obj = BytesIO(source_code.encode('utf-8'))
    out = []
    last_lineno = -1
    last_col = 0

    try:
        for tok in tokenize.tokenize(io_obj.readline):
            token_type = tok.type
            token_string = tok.string
            start_line, start_col = tok.start
            end_line, end_col = tok.end

            if start_line > last_lineno:
                last_col = 0
            
            # Preserva a indentação correta
            if start_col > last_col:
                out.append(" " * (start_col - last_col))

            # Pula comentários
            if token_type == tokenize.COMMENT:
                pass
            # Pula o token de encoding interno do Python (para não gerar lixo no início do arquivo)
            elif token_type == tokenize.ENCODING:
                pass
            # Opcional: Se quiser remover Docstrings (aquelas strings triplas """ texto """), descomente abaixo:
            # elif token_type == tokenize.STRING and (start_col == 0 or last_col == 0): 
            #     pass 
            else:
                out.append(token_string)

            last_col = end_col
            last_lineno = end_line
            
    except tokenize.TokenError:
        return source_code # Em caso de erro de sintaxe, retorna o original

    return "".join(out).lstrip()

def process_directory(directory):
    print(f"🧹 Iniciando limpeza de comentários em: {directory}")
    for root, dirs, files in os.walk(directory):
        if "venv" in dirs:
            dirs.remove("venv") # Ignora pasta venv
            
        for file in files:
            if file.endswith(".py") and file != "remove_comments.py":
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                clean_content = remove_comments_and_docstrings(content)
                
                if content != clean_content:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(clean_content)
                    print(f"✅ Limpo: {file}")

if __name__ == "__main__":
    # Pega o diretório onde o script está rodando
    current_dir = os.path.dirname(os.path.abspath(__file__))
    process_directory(current_dir)