from __future__ import annotations

_PT = {
    # ── App ──────────────────────────────────────────────────────────────────
    "app_title": "Iris",
    "language": "Idioma",

    # ── Tabs ─────────────────────────────────────────────────────────────────
    "tab_text_search": "Busca por texto",
    "tab_image_search": "Busca por imagem",
    "tab_gallery": "Galeria",
    "tab_duplicates": "Duplicatas",
    "tab_import": "Importar",
    "tab_collections": "Coleções",
    "tab_concepts": "Conceitos",
    "tab_backup": "Backup",

    # ── Sidebar ───────────────────────────────────────────────────────────────
    "sidebar_settings": "Configurações",
    "sidebar_database": "Banco de dados",
    "sidebar_no_db": "Nenhum banco encontrado. Rode `python -m core.indexer` primeiro.",
    "sidebar_media_folder": "Pasta de mídias",
    "sidebar_clip_model": "Modelo CLIP",
    "sidebar_search_strategy": "Estratégia de busca",
    "sidebar_top_results": "Resultados",
    "sidebar_media_type": "Tipo de mídia",
    "sidebar_all_media": "Todos",
    "sidebar_images": "Imagens",
    "sidebar_videos": "Vídeos",
    "sidebar_audio": "Áudio",
    "sidebar_album_filter": "Álbum",
    "sidebar_filter_by_album": "Filtrar por álbum",
    "sidebar_concept_filter": "Conceito",
    "sidebar_filter_by_concept": "Filtrar por conceito",
    "sidebar_balance": "Visual ↔ Texto",
    "sidebar_text_bonus": "Bônus texto",
    "sidebar_lexical": "Peso lexical",

    # ── Search ────────────────────────────────────────────────────────────────
    "search_placeholder": "Ex: sapo triste terno -gato",
    "search_label": "Buscar na sua galeria",
    "search_group_similar": "Agrupar resultados similares",
    "search_group_threshold": "Limiar do agrupamento",
    "search_show_singletons": "Mostrar grupos de 1 item",
    "search_upload_label": "Arraste uma imagem para buscar similares",
    "search_ref_image": "Imagem de referência",
    "search_spinner": "Buscando na sua galeria...",
    "search_similar_spinner": "Buscando similares...",
    "search_random_spinner": "Carregando aleatórios...",
    "search_empty_hint": "Digite uma busca ou envie uma imagem para começar.",
    "search_back": "Voltar para busca",
    "search_similar_info": "Mostrando resultados similares ao item selecionado.",
    "search_random_info": "Mostrando itens aleatórios da coleção.",
    "search_mode_balanced": "Equilibrado",
    "search_mode_text": "Foco no Texto",
    "search_mode_visual": "Foco Visual",

    # ── Results ───────────────────────────────────────────────────────────────
    "result_select": "Selecionar",
    "result_details": "Detalhes",
    "result_file": "Arquivo",
    "result_extracted_text": "Texto extraído",
    "result_tags": "Tags",
    "result_ai_description": "Descrição IA",
    "result_find_similar": "Buscar imagens similares",
    "result_open_file": "Abrir arquivo",
    "result_file_not_found": "Arquivo não encontrado.",
    "result_play": "▶ Reproduzir",
    "result_open_player": "Abrir no player",
    "result_play_error": "Não foi possível reproduzir.",
    "result_collections": "Coleções",
    "result_concepts": "Conceitos",
    "result_add_to_collection": "Adicionar à coleção",
    "result_remove_from_collection": "Remover",

    # ── Import ────────────────────────────────────────────────────────────────
    "import_folder_label": "Pasta para importar",
    "import_files_label": "Arquivos para importar",
    "import_album_dest": "Álbum de destino *(opcional)*",
    "import_album_desc": (
        "Todos os arquivos importados serão adicionados automaticamente a este álbum. "
        "Útil para conjuntos grandes. Se o álbum não existir, será criado."
    ),
    "import_album_mode_none": "Sem álbum",
    "import_album_mode_existing": "Álbum existente",
    "import_album_mode_new": "Criar novo álbum",
    "import_album_select": "Selecione o álbum",
    "import_album_no_existing": "Nenhum álbum criado ainda. Escolha 'Criar novo álbum'.",
    "import_album_new_placeholder": "Ex: Prints do meu antigo celular",
    "import_album_new_label": "Nome do novo álbum",
    "import_album_will_create": "Será criado o álbum **{name}** e todos os arquivos serão adicionados a ele.",
    "import_library": "Biblioteca",
    "import_library_root": "Raiz das bibliotecas",
    "import_recursive": "Importar subpastas",
    "import_batch_size": "Batch size",
    "import_device": "Dispositivo",
    "import_caption_model": "Modelo de caption",
    "import_caption_model_help": "Use 'none' para desativar.",
    "import_whisper_model": "Modelo Whisper",
    "import_whisper_model_help": "Use 'none' para desativar transcrição de áudio.",
    "import_button": "Importar e indexar",
    "import_button_album": "Importar e indexar → álbum '{name}'",
    "import_invalid_folder": "Pasta inválida: {path}",
    "import_no_source": "Nenhuma fonte informada para importação.",
    "import_start_error": "Falha ao iniciar importação: {error}",

    # ── Import progress (taskbar) ─────────────────────────────────────────────
    "indexing_title": "⏳ Indexação em andamento",
    "indexing_background": "Importação em andamento em segundo plano. Você pode continuar usando as outras abas normalmente.",
    "indexing_files": "{done} / {total} arquivo(s)",
    "indexing_eta": "Tempo restante: **{eta}**",
    "indexing_loading": "Carregando modelos de IA…",
    "indexing_done": "✓ {total} arquivo(s) indexado(s).",
    "indexing_done_album": "✓ {total} arquivo(s) indexado(s). Álbum **{name}** atualizado.",
    "indexing_error": "Erro na indexação: {error}",

    # ── Interrupted imports ────────────────────────────────────────────────────
    "pending_title": "Importações interrompidas",
    "pending_desc": (
        "Estas importações foram iniciadas em sessões anteriores e podem ser retomadas. "
        "Arquivos já indexados serão ignorados automaticamente."
    ),
    "pending_album_label": "álbum {name}",
    "pending_started": "Iniciada em {date}",
    "pending_resume": "Retomar",
    "pending_discard": "Descartar",
    "pending_missing": "Pastas não encontradas (ignoradas): {paths}",

    # ── Gallery ───────────────────────────────────────────────────────────────
    "gallery_cols": "Colunas",
    "gallery_random": "Modo aleatório",
    "gallery_random_stop": "Parar modo aleatório",

    # ── Duplicates ─────────────────────────────────────────────────────────────
    "dup_min_similarity": "Similaridade mínima",
    "dup_neighbors": "Vizinhos por imagem",
    "dup_min_group": "Tamanho mínimo do grupo",
    "dup_view_mode": "Visualização",
    "dup_view_groups": "Por grupos",
    "dup_view_flat": "Lista única",
    "dup_sort": "Ordenação",
    "dup_sort_similarity": "Similaridade",
    "dup_sort_newest": "Data (mais nova)",
    "dup_sort_oldest": "Data (mais antiga)",
    "dup_find": "Encontrar duplicatas",
    "dup_clear": "Limpar duplicatas",
    "dup_spinner": "Agrupando duplicatas e quase-duplicatas...",
    "dup_cleanup_title": "Revisão de limpeza — {groups} grupo(s), {copies} cópia(s)",
    "dup_cleanup_desc": "A imagem mais antiga de cada grupo é a original. As cópias estão pré-selecionadas para remoção. Desmarque as que quiser manter.",
    "dup_original_label": "Original",
    "dup_oldest": "mais antigo",
    "dup_copies_label": "Cópias",
    "dup_remove_label": "Remover",
    "dup_confirm_remove": "Mover {count} cópia(s) para a lixeira",
    "dup_cancel": "Cancelar",
    "dup_trash_success": "{count} arquivo(s) enviado(s) para a lixeira.",
    "dup_trash_failed": "{count} falha(s) ao mover para a lixeira.",

    # ── Collections ────────────────────────────────────────────────────────────
    "col_title": "Coleções",
    "col_new": "Nova coleção",
    "col_name": "Nome",
    "col_description": "Descrição",
    "col_create": "Criar coleção",
    "col_delete": "Excluir coleção",
    "col_confirm_delete": "Confirmar exclusão",

    # ── Concepts ──────────────────────────────────────────────────────────────
    "concept_title": "Conceitos",
    "concept_new": "Criar novo conceito",
    "concept_name": "Nome",
    "concept_category": "Categoria",
    "concept_description": "Descrição",
    "concept_search_terms": "Termos de busca extras",
    "concept_threshold": "Limiar de auto-associação",
    "concept_add_refs": "Adicionar imagens de referência",
    "concept_find_matches": "Encontrar matches automáticos",
    "concept_apply": "Aplicar",
    "concept_reject": "Rejeitar",
    "concept_delete": "Excluir conceito",
    "concept_confirmed": "Associações confirmadas",
    "concept_remove_assoc": "Remover associação",

    # ── General actions ────────────────────────────────────────────────────────
    "action_save": "Salvar",
    "action_cancel": "Cancelar",
    "action_confirm": "Confirmar",
    "action_delete": "Excluir",
    "action_send_trash": "Mover para lixeira",
    "action_clear_selection": "Limpar seleção",
    "action_select_all": "Selecionar todos",
    "action_load_more": "Carregar mais ({n} restantes)",

    # ── Feedback ──────────────────────────────────────────────────────────────
    "feedback_import_done": "Importação concluída. {count} arquivo(s) indexados.",
    "feedback_import_done_album": "Importação concluída. Arquivos adicionados ao álbum '{name}'.",
    "feedback_trash_done": "{n} arquivo(s) movido(s) para a lixeira.",
    "feedback_trash_error": "Erro ao mover para lixeira: {error}",
}

_EN: dict[str, str] = {
    # ── App ──────────────────────────────────────────────────────────────────
    "app_title": "Iris",
    "language": "Language",

    # ── Tabs ─────────────────────────────────────────────────────────────────
    "tab_text_search": "Text search",
    "tab_image_search": "Image search",
    "tab_gallery": "Gallery",
    "tab_duplicates": "Duplicates",
    "tab_import": "Import",
    "tab_collections": "Collections",
    "tab_concepts": "Concepts",
    "tab_backup": "Backup",

    # ── Sidebar ───────────────────────────────────────────────────────────────
    "sidebar_settings": "Settings",
    "sidebar_database": "Database",
    "sidebar_no_db": "No database found. Run `python -m core.indexer` first.",
    "sidebar_media_folder": "Media folder",
    "sidebar_clip_model": "CLIP Model",
    "sidebar_search_strategy": "Search strategy",
    "sidebar_top_results": "Results",
    "sidebar_media_type": "Media type",
    "sidebar_all_media": "All",
    "sidebar_images": "Images",
    "sidebar_videos": "Videos",
    "sidebar_audio": "Audio",
    "sidebar_album_filter": "Album",
    "sidebar_filter_by_album": "Filter by album",
    "sidebar_concept_filter": "Concept",
    "sidebar_filter_by_concept": "Filter by concept",
    "sidebar_balance": "Visual ↔ Text",
    "sidebar_text_bonus": "Text bonus",
    "sidebar_lexical": "Lexical weight",

    # ── Search ────────────────────────────────────────────────────────────────
    "search_placeholder": "E.g.: sad frog in a suit -cat",
    "search_label": "Search your gallery",
    "search_group_similar": "Group similar results",
    "search_group_threshold": "Grouping threshold",
    "search_show_singletons": "Show single-item groups",
    "search_upload_label": "Drop an image to find visually similar files",
    "search_ref_image": "Reference image",
    "search_spinner": "Searching your gallery...",
    "search_similar_spinner": "Finding similar items...",
    "search_random_spinner": "Loading random items...",
    "search_empty_hint": "Enter a query or upload an image to start.",
    "search_back": "Back to search",
    "search_similar_info": "Showing results similar to the selected item.",
    "search_random_info": "Showing random items from the collection.",
    "search_mode_balanced": "Balanced",
    "search_mode_text": "Text focus",
    "search_mode_visual": "Visual focus",

    # ── Results ───────────────────────────────────────────────────────────────
    "result_select": "Select",
    "result_details": "Details",
    "result_file": "File",
    "result_extracted_text": "Extracted text",
    "result_tags": "Tags",
    "result_ai_description": "AI description",
    "result_find_similar": "Find similar images",
    "result_open_file": "Open file",
    "result_file_not_found": "File not found.",
    "result_play": "▶ Play",
    "result_open_player": "Open in player",
    "result_play_error": "Could not play this file.",
    "result_collections": "Collections",
    "result_concepts": "Concepts",
    "result_add_to_collection": "Add to collection",
    "result_remove_from_collection": "Remove",

    # ── Import ────────────────────────────────────────────────────────────────
    "import_folder_label": "Folder to import",
    "import_files_label": "Files to import",
    "import_album_dest": "Destination album *(optional)*",
    "import_album_desc": (
        "All imported files will be automatically added to this album. "
        "Useful for large sets. The album will be created if it doesn't exist."
    ),
    "import_album_mode_none": "No album",
    "import_album_mode_existing": "Existing album",
    "import_album_mode_new": "Create new album",
    "import_album_select": "Select album",
    "import_album_no_existing": "No albums yet. Choose 'Create new album'.",
    "import_album_new_placeholder": "E.g.: Old phone screenshots",
    "import_album_new_label": "New album name",
    "import_album_will_create": "Album **{name}** will be created and all files added to it.",
    "import_library": "Library",
    "import_library_root": "Library root",
    "import_recursive": "Import subfolders",
    "import_batch_size": "Batch size",
    "import_device": "Device",
    "import_caption_model": "Caption model",
    "import_caption_model_help": "Use 'none' to disable.",
    "import_whisper_model": "Whisper model",
    "import_whisper_model_help": "Use 'none' to disable audio transcription.",
    "import_button": "Import and index",
    "import_button_album": "Import and index → album '{name}'",
    "import_invalid_folder": "Invalid folder: {path}",
    "import_no_source": "No source specified for import.",
    "import_start_error": "Failed to start import: {error}",

    # ── Import progress (taskbar) ─────────────────────────────────────────────
    "indexing_title": "⏳ Indexing in progress",
    "indexing_background": "Import running in the background. You can keep using the app normally.",
    "indexing_files": "{done} / {total} file(s)",
    "indexing_eta": "Time remaining: **{eta}**",
    "indexing_loading": "Loading AI models…",
    "indexing_done": "✓ {total} file(s) indexed.",
    "indexing_done_album": "✓ {total} file(s) indexed. Album **{name}** updated.",
    "indexing_error": "Indexing error: {error}",

    # ── Interrupted imports ────────────────────────────────────────────────────
    "pending_title": "Interrupted imports",
    "pending_desc": (
        "These imports were started in previous sessions and can be resumed. "
        "Already-indexed files will be skipped automatically."
    ),
    "pending_album_label": "album {name}",
    "pending_started": "Started at {date}",
    "pending_resume": "Resume",
    "pending_discard": "Discard",
    "pending_missing": "Folders not found (skipped): {paths}",

    # ── Gallery ───────────────────────────────────────────────────────────────
    "gallery_cols": "Columns",
    "gallery_random": "Random mode",
    "gallery_random_stop": "Stop random mode",

    # ── Duplicates ─────────────────────────────────────────────────────────────
    "dup_min_similarity": "Minimum similarity",
    "dup_neighbors": "Neighbors per image",
    "dup_min_group": "Minimum group size",
    "dup_view_mode": "View mode",
    "dup_view_groups": "By groups",
    "dup_view_flat": "Flat list",
    "dup_sort": "Sort by",
    "dup_sort_similarity": "Similarity",
    "dup_sort_newest": "Date (newest)",
    "dup_sort_oldest": "Date (oldest)",
    "dup_find": "Find duplicates",
    "dup_clear": "Clear duplicates",
    "dup_spinner": "Grouping duplicates and near-duplicates...",
    "dup_cleanup_title": "Cleanup review — {groups} group(s), {copies} copy(ies)",
    "dup_cleanup_desc": "The oldest image in each group is the original. Copies are pre-selected for removal. Uncheck any you want to keep.",
    "dup_original_label": "Original",
    "dup_oldest": "oldest",
    "dup_copies_label": "Copies",
    "dup_remove_label": "Remove",
    "dup_confirm_remove": "Move {count} copy(ies) to trash",
    "dup_cancel": "Cancel",
    "dup_trash_success": "{count} file(s) sent to trash.",
    "dup_trash_failed": "{count} failure(s) moving to trash.",

    # ── Collections ────────────────────────────────────────────────────────────
    "col_title": "Collections",
    "col_new": "New collection",
    "col_name": "Name",
    "col_description": "Description",
    "col_create": "Create collection",
    "col_delete": "Delete collection",
    "col_confirm_delete": "Confirm deletion",

    # ── Concepts ──────────────────────────────────────────────────────────────
    "concept_title": "Concepts",
    "concept_new": "Create new concept",
    "concept_name": "Name",
    "concept_category": "Category",
    "concept_description": "Description",
    "concept_search_terms": "Extra search terms",
    "concept_threshold": "Auto-match threshold",
    "concept_add_refs": "Add reference images",
    "concept_find_matches": "Find automatic matches",
    "concept_apply": "Apply",
    "concept_reject": "Reject",
    "concept_delete": "Delete concept",
    "concept_confirmed": "Confirmed associations",
    "concept_remove_assoc": "Remove association",

    # ── General actions ────────────────────────────────────────────────────────
    "action_save": "Save",
    "action_cancel": "Cancel",
    "action_confirm": "Confirm",
    "action_delete": "Delete",
    "action_send_trash": "Move to trash",
    "action_clear_selection": "Clear selection",
    "action_select_all": "Select all",
    "action_load_more": "Load more ({n} remaining)",

    # ── Feedback ──────────────────────────────────────────────────────────────
    "feedback_import_done": "Import complete. {count} file(s) indexed.",
    "feedback_import_done_album": "Import complete. Files added to album '{name}'.",
    "feedback_trash_done": "{n} file(s) moved to trash.",
    "feedback_trash_error": "Error moving to trash: {error}",
}

TRANSLATIONS: dict[str, dict[str, str]] = {"pt": _PT, "en": _EN}
SUPPORTED_LANGUAGES = {"pt": "🇧🇷 PT", "en": "🇬🇧 EN"}


def get_text(key: str, lang: str = "pt", **kwargs: object) -> str:
    """Return translated string for key in lang, with optional format kwargs."""
    table = TRANSLATIONS.get(lang, _PT)
    text = table.get(key) or _PT.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text
