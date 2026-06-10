import os

def is_ignored(name):
    """Define pastas e arquivos que não interessam para a arquitetura."""
    ignored = {
        'venv', '.git', '__pycache__', '.idea', '.vscode', 
        'iris.db', '*.faiss', '*.png', '*.jpg', '*.mp4'
    }
    return name in ignored or name.endswith(('.pyc', '.db', '.faiss', '.png', '.jpg', '.jpeg', '.mp4', '.webm'))

def generate_project_map(startpath):
    output = []
    output.append(f"📂 MAPA DO PROJETO: {os.path.basename(startpath)}\n")
    
    for root, dirs, files in os.walk(startpath):
        # Modifica a lista 'dirs' in-place para pular pastas ignoradas no walk
        dirs[:] = [d for d in dirs if not is_ignored(d)]
        
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * (level)
        subindent = ' ' * 4 * (level + 1)
        
        folder_name = os.path.basename(root)
        if level == 0: folder_name = "."
        
        output.append(f"{indent}📁 {folder_name}/")
        
        for f in files:
            if is_ignored(f):
                continue
                
            output.append(f"{subindent}📄 {f}")
            
            # Se for Python, extrai os imports para mostrar dependências
            if f.endswith(".py"):
                try:
                    path = os.path.join(root, f)
                    with open(path, "r", encoding="utf-8", errors="ignore") as file_content:
                        imports = [line.strip() for line in file_content if line.strip().startswith(("import ", "from "))]
                        if imports:
                            output.append(f"{subindent}   Dependencies: {', '.join(imports)[:200]}...") # Corta se for muito longo
                except Exception:
                    pass

    return "\n".join(output)

if __name__ == "__main__":
    print(generate_project_map(os.getcwd()))