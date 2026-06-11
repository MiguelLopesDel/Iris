"""Iris — local multimodal AI media librarian.

Entry point and orchestrator. Each tab delegates to its own module under app.tabs.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections import Counter
from hashlib import md5
from pathlib import Path

import numpy as np
import streamlit as st
from PIL import Image

from app.components import (
    clear_selection_state,
    clear_video_state,
    render_import_feedback,
    render_media,
    render_trash_feedback,
    selection_key,
    thumb_b64,
    video_thumbnail,
)
from app.tabs.collections import _render_result_collections, render_collections_tab
from app.tabs.concepts import _render_result_concepts, render_concepts_tab
from app.tabs.duplicates import (
    clear_duplicate_state,
    filter_duplicate_groups,
    render_duplicate_flat,
    render_duplicate_groups,
    sort_duplicate_groups,
)
from app.tabs.gallery import render_gallery_tab
from app.tabs.import_tab import render_folder_browser, run_import_job
from app.tabs.backup import render_backup_tab
from app.tabs.search import (
    group_search_results,
    render_grouped_search_results,
    render_result,
    render_results,
)
from core.backend import SearchBackend, create_backend
from core.duplicates import find_duplicate_groups
from core.file_ops import move_to_trash, to_file_uri
from core.indexer import DEFAULT_LIBRARY_NAME, DEFAULT_LIBRARY_ROOT
from core.perf import trace, dump as dump_perf
from core.search_engine import (
    DEFAULT_MODEL,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    SearchOptions,
    SearchResult,
)

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Iris", layout="wide", page_icon="🖼️")

st.markdown(
    """
    <style>
    .stImage { border-radius: 8px; transition: transform .2s; }
    .stImage:hover { transform: scale(1.01); }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Engine loader ────────────────────────────────────────────────────────────


def get_available_databases(data_dir: str = "data") -> list[str]:
    root = Path(data_dir)
    root.mkdir(exist_ok=True)
    files = sorted(root.rglob("*.db"))
    return sorted([path.relative_to(root).as_posix() for path in files], reverse=True)


@st.cache_resource(show_spinner="Carregando modelo e indice...")
def load_engine(db_path: str, model_name: str, media_root: str) -> SearchBackend:
    return create_backend(db_path=db_path, model_name=model_name, media_root=media_root)


# ── Session helpers ──────────────────────────────────────────────────────────


def _selected_record_indices(backend: SearchBackend) -> list[int]:
    indices: list[int] = []
    for key, value in st.session_state.items():
        if value and key.startswith("select_"):
            try:
                idx = int(key.split("_", 1)[1])
                if 0 <= idx < len(backend.get_all_records()):
                    indices.append(idx)
            except (IndexError, ValueError):
                continue
    return indices


def selected_record_paths(backend: SearchBackend) -> list[str]:
    selected: list[str] = []
    for key, value in st.session_state.items():
        if not value or not key.startswith("select_"):
            continue
        try:
            index = int(key.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if index < 0 or index >= len(backend.get_all_records()):
            continue
        path = backend.get_all_records()[index].resolved_path
        if path and os.path.exists(path):
            selected.append(path)
    return sorted(dict.fromkeys(selected))


def selected_record_db_ids(backend: SearchBackend) -> list[int]:
    db_ids: list[int] = []
    for key, value in st.session_state.items():
        if not value or not key.startswith("select_"):
            continue
        try:
            index = int(key.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if index < 0 or index >= len(backend.get_all_records()):
            continue
        db_id = backend.get_all_records()[index].db_id
        if db_id:
            db_ids.append(db_id)
    return sorted(dict.fromkeys(db_ids))


def _search_mode_options(mode: str, backend: SearchBackend) -> tuple[float, float, float]:
    if mode == "Foco no Texto":
        return 0.0, 3.0, 0.4
    if mode == "Foco Visual":
        return 0.65, 0.5, 0.0
    return (
        float(backend.weights.get("balance", 0.65)),
        float(backend.weights.get("text_bonus", 1.0)),
        float(backend.weights.get("lexical_weight", 0.0)),
    )


def trash_selected(backend: SearchBackend) -> None:
    selected_paths = selected_record_paths(backend)
    moved, failed = move_to_trash(selected_paths)
    st.session_state["trash_feedback"] = {"moved": moved, "failed": failed}
    clear_selection_state()
    st.cache_resource.clear()
    st.session_state.pop("similar_index", None)
    clear_duplicate_state()
    st.session_state.pop("search_results", None)
    st.session_state.pop("search_results_key", None)
    st.session_state.pop("_gallery_cache", None)
    st.rerun()


# ── Search cache ─────────────────────────────────────────────────────────────


def _options_fingerprint(options: SearchOptions) -> str:
    col = ",".join(str(i) for i in sorted(options.collection_ids)) if options.collection_ids else ""
    cpt = ",".join(str(i) for i in sorted(options.concept_ids)) if options.concept_ids else ""
    return (
        f"top_k={options.top_k}|thr={options.threshold:.4f}|bal={options.balance:.4f}|"
        f"tb={options.text_bonus:.4f}|lw={options.lexical_weight:.4f}|tr={int(options.translate)}|"
        f"col={col}|mt={options.media_type}|cpt={cpt}"
    )


def _get_cached_results(cache_key: str) -> list[SearchResult] | None:
    if st.session_state.get("search_results_key") != cache_key:
        return None
    cached = st.session_state.get("search_results")
    if not isinstance(cached, list):
        return None
    return cached


def _set_cached_results(cache_key: str, results: list[SearchResult]) -> None:
    st.session_state["search_results_key"] = cache_key
    st.session_state["search_results"] = results


# ── Floating panel ───────────────────────────────────────────────────────────


def _build_floating_panel(backend: SearchBackend) -> None:
    selected_indices = _selected_record_indices(backend)
    n = len(selected_indices)
    if n == 0:
        return

    thumbs_html = ""
    for idx in selected_indices[:8]:
        record = backend.get_all_records()[idx]
        fp = record.resolved_path
        ex = bool(fp and os.path.exists(fp))
        ext = os.path.splitext(fp or record.caminho)[1].lower()
        b64 = thumb_b64(fp, ext in VIDEO_EXTENSIONS) if ex and fp else ""
        title = record.arquivo.replace('"', "").replace("<", "").replace(">", "")[:30]
        if b64:
            thumbs_html += (
                f'<img src="data:image/jpeg;base64,{b64}" title="{title}" '
                f'style="width:58px;height:58px;object-fit:cover;border-radius:7px;margin:3px;">'
            )
        else:
            icon = "🎬" if ext in VIDEO_EXTENSIONS else "📷"
            thumbs_html += (
                f'<span title="{title}" style="display:inline-flex;width:58px;height:58px;'
                f'background:#2a2a3e;border-radius:7px;margin:3px;'
                f'align-items:center;justify-content:center;font-size:22px;">{icon}</span>'
            )

    extra = (
        f'<div style="color:#9999bb;font-size:11px;margin-top:5px;">... e mais {n - 8}</div>'
        if n > 8
        else ""
    )
    uid = f"mcf{n}"
    st.markdown(
        f"""
        <style>
        #{uid}-chk {{ display:none; }}
        #{uid}-panel {{ display:none; }}
        #{uid}-chk:checked ~ #{uid}-panel {{ display:block; }}
        #{uid}-badge {{ cursor:pointer; user-select:none; }}
        </style>
        <div style="position:fixed;bottom:22px;right:22px;z-index:99999;
                    display:flex;flex-direction:column-reverse;align-items:flex-end;gap:8px;">
          <input type="checkbox" id="{uid}-chk">
          <label for="{uid}-chk" id="{uid}-badge" style="
              background:#e63946;color:#fff;border-radius:50px;
              padding:10px 20px;font-size:14px;font-weight:700;
              box-shadow:0 4px 16px rgba(0,0,0,.4);white-space:nowrap;
              font-family:sans-serif;display:inline-block;">
            🗂 {n} selecionado(s)
          </label>
          <div id="{uid}-panel" style="
              background:#16213e;border:1px solid #3a3a5c;border-radius:14px;
              padding:14px;max-width:340px;
              box-shadow:0 6px 24px rgba(0,0,0,.5);font-family:sans-serif;">
            <div style="color:#fff;font-weight:700;font-size:13px;margin-bottom:8px;">
              Itens selecionados
            </div>
            <div style="display:flex;flex-wrap:wrap;">{thumbs_html}</div>
            {extra}
            <div style="color:#7777aa;font-size:11px;margin-top:10px;line-height:1.5;">
              Use a barra lateral para aplicar acoes ↖
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── Sidebar panel ────────────────────────────────────────────────────────────


def _render_sidebar_selection_panel(backend: SearchBackend) -> None:
    selected_indices = _selected_record_indices(backend)
    n = len(selected_indices)

    st.sidebar.markdown("### Selecao")
    st.sidebar.caption(f"{n} item(ns) selecionado(s)")

    if n == 0:
        st.sidebar.caption("Nenhum item selecionado.")
        return

    if st.sidebar.button("Limpar selecao", key="sb_clear_sel"):
        clear_selection_state()
        st.rerun()

    if st.sidebar.button(
        "Mover selecionadas para lixeira",
        key="sb_trash_sel",
        help="Move para a lixeira do sistema. Nao usa rm.",
    ):
        trash_selected(backend)

    with st.sidebar.expander(f"Ver {n} item(ns) selecionado(s)"):
        for idx in selected_indices[:12]:
            record = backend.get_all_records()[idx]
            fp = record.resolved_path
            ex = bool(fp and os.path.exists(fp))
            ext = os.path.splitext(fp or record.caminho)[1].lower()
            if ex and ext not in VIDEO_EXTENSIONS:
                try:
                    st.sidebar.image(fp, width=120)
                except Exception:
                    st.sidebar.caption("📷 " + record.arquivo[:20])
            else:
                thumb = video_thumbnail(fp) if ex else None
                if thumb:
                    st.sidebar.image(thumb, width=120)
                else:
                    st.sidebar.caption(("🎬 " if ext in VIDEO_EXTENSIONS else "❓ ") + record.arquivo[:20])
        if n > 12:
            st.sidebar.caption(f"... e mais {n - 12} item(ns)")

    # Add to collection
    collections = backend.list_collections()
    if collections:
        col_options = {c["name"]: c["id"] for c in collections}
        target_col = st.sidebar.selectbox(
            "Adicionar a colecao",
            options=["— escolha —"] + list(col_options.keys()),
            key="sidebar_add_to_col",
        )
        if st.sidebar.button("Adicionar selecionadas", key="sb_add_to_col", disabled=target_col == "— escolha —"):
            db_ids = selected_record_db_ids(backend)
            if db_ids and target_col in col_options:
                added = backend.add_records_to_collection(db_ids, col_options[target_col])
                st.sidebar.success(f"{added} item(ns) adicionado(s).")
                st.rerun()


# ── Video volume sync ────────────────────────────────────────────────────────


def _inject_video_volume_sync() -> None:
    import streamlit.components.v1 as components

    components.html(
        """
        <script>
        (function() {
            var DEFAULT_VOL = 0.3;
            var syncing = false;
            function initVideo(video) {
                if (video._mcVol) return;
                video._mcVol = true;
                video.volume = DEFAULT_VOL;
                video.addEventListener('volumechange', function() {
                    if (syncing) return;
                    syncing = true;
                    var vol = video.volume;
                    try {
                        window.parent.document.querySelectorAll('video').forEach(function(v) {
                            if (v !== video) v.volume = vol;
                        });
                    } catch(e) {}
                    syncing = false;
                });
            }
            function initAll() {
                try { window.parent.document.querySelectorAll('video').forEach(initVideo); } catch(e) {}
            }
            setTimeout(initAll, 800);
            try {
                var obs = new MutationObserver(function() { initAll(); });
                obs.observe(window.parent.document.body, { childList: true, subtree: true });
            } catch(e) {}
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    # State init
    if "duplicate_mode" not in st.session_state:
        st.session_state["duplicate_mode"] = False
    if "random_mode" not in st.session_state:
        st.session_state["random_mode"] = False

    # ── Sidebar: DB & model ──────────────────────────────────────────────
    with trace("sidebar.config"):
        st.sidebar.title("Configuracoes")
        databases = get_available_databases()
    if not databases:
        st.sidebar.error("Nenhum banco encontrado. Rode `python -m core.indexer` primeiro.")
        st.stop()

    selected_db = st.sidebar.selectbox("Banco de dados", databases)
    db_path = os.path.join("data", selected_db)

    _prev_db = st.session_state.get("_active_db")
    if _prev_db is not None and _prev_db != db_path:
        clear_selection_state()
        clear_video_state()
    st.session_state["_active_db"] = db_path
    media_root = st.sidebar.text_input("Pasta de midias", value="media")
    model_name = st.sidebar.text_input("Modelo CLIP", value=DEFAULT_MODEL)

    with trace("sidebar.db_load"):
        try:
            backend = load_engine(db_path, model_name, media_root)
        except Exception as exc:
            st.error(f"Falha ao carregar o indice: {exc}")
            st.stop()

    # ── Sidebar: status ──────────────────────────────────────────────────
    st.sidebar.title("Status")
    st.sidebar.info(f"{len(backend.get_all_records())} itens indexados")
    missing = sum(1 for r in backend.get_all_records() if not r.resolved_path or not os.path.exists(r.resolved_path))
    if missing:
        st.sidebar.warning(f"{missing} arquivos nao encontrados no disco")

    # ── Sidebar: filters ─────────────────────────────────────────────────
    with trace("sidebar.filters"):
        all_collections = backend.list_collections()
    collection_filter_ids: frozenset[int] = frozenset()
    if all_collections:
        st.sidebar.markdown("### Colecao")
        col_names = [c["name"] for c in all_collections]
        selected_col_names = st.sidebar.multiselect(
            "Filtrar por colecao", options=col_names, default=[],
            key="collection_filter",
            help="Deixe vazio para buscar em todos os itens.",
        )
        if selected_col_names:
            name_to_id = {c["name"]: c["id"] for c in all_collections}
            collection_filter_ids = frozenset(name_to_id[n] for n in selected_col_names if n in name_to_id)
        # Detect filter change → invalidate search cache so results refresh immediately
        _prev = st.session_state.get("_prev_collection_filter_ids")
        if _prev is not None and _prev != collection_filter_ids:
            st.session_state.pop("search_results", None)
            st.session_state.pop("search_results_key", None)
        st.session_state["_prev_collection_filter_ids"] = collection_filter_ids

    all_concepts = backend.list_concepts()
    concept_filter_ids: frozenset[int] = frozenset()
    if all_concepts:
        st.sidebar.markdown("### Conceito")
        cpt_names = [c["name"] for c in all_concepts]
        selected_cpt_names = st.sidebar.multiselect(
            "Filtrar por conceito", options=cpt_names, default=[],
            key="concept_filter",
            help="Mostra apenas midias associadas ao conceito.",
        )
        if selected_cpt_names:
            cpt_name_to_id = {c["name"]: c["id"] for c in all_concepts}
            concept_filter_ids = frozenset(cpt_name_to_id[n] for n in selected_cpt_names if n in cpt_name_to_id)
        # Detect filter change → invalidate search cache
        _prev_cpt = st.session_state.get("_prev_concept_filter_ids")
        if _prev_cpt is not None and _prev_cpt != concept_filter_ids:
            st.session_state.pop("search_results", None)
            st.session_state.pop("search_results_key", None)
        st.session_state["_prev_concept_filter_ids"] = concept_filter_ids

    _render_sidebar_selection_panel(backend)

    if st.sidebar.checkbox("Estatisticas"):
        exts = Counter(os.path.splitext(r.arquivo)[1].lower() for r in backend.get_all_records())
        st.sidebar.bar_chart(exts)

    # ── Sidebar: search params ───────────────────────────────────────────
    threshold = st.sidebar.slider("Limiar de similaridade", -1.0, 1.0, 0.15)
    top_k = int(st.sidebar.number_input("Quantidade de resultados", 1, 200, 50))

    st.sidebar.markdown("### Tipo de midia")
    media_type = st.sidebar.radio("Mostrar", ["Tudo", "Imagens", "Videos"], index=0, horizontal=True)
    media_type_value = {"Tudo": "all", "Imagens": "image", "Videos": "video"}[media_type]

    translate_enabled = st.sidebar.checkbox(
        "Traduzir busca para ingles",
        value=True,
        help="Usa Google Translate para converter queries PT → EN. "
             "Desmarque para privacidade total ou se estiver offline.",
    )

    st.sidebar.markdown("### Estrategia")
    mode = st.sidebar.radio("Modo", ["Hibrido", "Foco no Texto", "Foco Visual", "Personalizado"], index=0)
    if mode == "Personalizado":
        balance = st.sidebar.slider("Visual vs conceitual", 0.0, 1.0, 0.5)
        text_bonus = st.sidebar.slider("Peso do texto exato", 0.0, 4.0, 2.0)
        lexical_weight = st.sidebar.slider("Peso lexical", 0.0, 1.0, 0.25)
    else:
        balance, text_bonus, lexical_weight = _search_mode_options(mode, backend)

    options = SearchOptions(
        top_k=top_k, threshold=threshold, balance=balance,
        text_bonus=text_bonus, lexical_weight=lexical_weight, translate=translate_enabled,
        collection_ids=collection_filter_ids, concept_ids=concept_filter_ids,
        media_type=media_type_value,
    )

    # ── Sidebar: actions ─────────────────────────────────────────────────
    if st.sidebar.button("Atualizar dados"):
        st.cache_resource.clear()
        clear_selection_state()
        clear_video_state()
        st.session_state.pop("similar_index", None)
        clear_duplicate_state()
        st.session_state.pop("search_results", None)
        st.session_state.pop("search_results_key", None)
        st.session_state.pop("_gallery_cache", None)
        st.rerun()

    if st.sidebar.button("Me surpreenda"):
        st.session_state["random_mode"] = True
        st.session_state.pop("similar_index", None)
        clear_duplicate_state()
        st.session_state.pop("search_results", None)
        st.session_state.pop("search_results_key", None)
        st.rerun()

    # ── Main content ─────────────────────────────────────────────────────
    st.title("Iris")
    with trace("ui.floating_panel"):
        _inject_video_volume_sync()
        _build_floating_panel(backend)
        render_trash_feedback()
        render_import_feedback()

    # ── Tabs ─────────────────────────────────────────────────────────────
    with trace("ui.tabs"):
        tab_text, tab_image, tab_gallery, tab_dupes, tab_import, tab_cols, tab_cpts, tab_bk = st.tabs(
            ["Busca por texto", "Busca por imagem", "Galeria", "Duplicatas", "Importar", "Colecoes", "Conceitos", "Backup"]
        )
    with tab_text:
        query = st.text_input(
            "Descreva o meme",
            placeholder="Ex: bebe decepcionado com nascimento -gato",
            key="query",
        )
    with tab_image:
        group_image_results = st.checkbox("Agrupar resultados similares", value=True)
        image_group_threshold = st.slider("Limiar do agrupamento", 0.75, 0.99, 0.90, 0.01)
        image_group_show_singletons = st.checkbox("Mostrar grupos de 1 imagem", value=False)
        uploaded_file = st.file_uploader(
            "Arraste uma imagem para encontrar similares",
            type=["png", "jpg", "jpeg", "webp"],
        )
        if uploaded_file:
            preview = Image.open(uploaded_file).convert("RGB")
            st.image(preview, caption="Imagem de referencia", width=220)
    with tab_gallery:
        with trace("ui.tab_gallery"):
            render_gallery_tab(backend, options)
    with tab_dupes:
        duplicate_threshold = st.slider("Similaridade minima", 0.90, 1.0, 0.985, 0.001)
        duplicate_neighbors = int(st.number_input("Vizinhos por imagem", min_value=2, max_value=50, value=12))
        duplicate_min_group_size = int(st.number_input("Tamanho minimo do grupo", min_value=2, max_value=500, value=2))
        duplicate_view_mode = st.radio("Visualizacao", ["Por grupos", "Lista unica"], horizontal=True)
        duplicate_sort_mode = st.selectbox("Ordenacao", ["Similaridade", "Data (mais nova)", "Data (mais antiga)"], index=0)
        col_find, col_clear = st.columns(2)
        with col_find:
            show_duplicates = st.button("Encontrar duplicatas")
        with col_clear:
            clear_duplicates = st.button("Limpar duplicatas")
    with tab_import:
        st.markdown("**Pasta para importar**")
        import_folder = render_folder_browser(prefix="import_fb")
        import_files = st.file_uploader(
            "Arquivos para importar",
            type=["png", "jpg", "jpeg", "webp", "gif", "mp4", "webm", "mkv", "mov"],
            accept_multiple_files=True,
            key="import_files_uploader",
        )
        import_library = st.text_input("Biblioteca", value=DEFAULT_LIBRARY_NAME)
        import_library_root = st.text_input("Raiz das bibliotecas", value=str(DEFAULT_LIBRARY_ROOT))
        import_recursive = st.checkbox("Importar subpastas", value=True)
        import_batch_size = int(st.number_input("Batch size", min_value=1, max_value=64, value=8))
        import_device = st.selectbox("Dispositivo", ["auto", "cuda", "mps", "cpu"], index=0)
        import_caption_model = st.text_input("Modelo de caption", value="microsoft/Florence-2-large", help="Use 'none' para desativar.")
        import_whisper_model = st.text_input("Modelo Whisper", value="tiny", help="Use 'none' para desativar transcricao de audio.")
        run_import = st.button("Importar e indexar", key="run_import_button")
    with tab_cols:
        render_collections_tab(backend)
    with tab_cpts:
        render_concepts_tab(backend)
    with tab_bk:
        render_backup_tab()

    # ── Search / results ─────────────────────────────────────────────────
    with trace("search.execute"):
        results: list[SearchResult] = []
    try:
        if run_import:
            _execute_import(
                db_path=db_path, import_folder=import_folder, import_files=import_files,
                import_library=import_library, import_library_root=import_library_root,
                import_recursive=import_recursive, import_batch_size=import_batch_size,
                import_device=import_device, model_name=model_name,
                import_caption_model=import_caption_model, import_whisper_model=import_whisper_model,
            )

        has_search_intent = bool(
            query or uploaded_file
            or ("similar_index" in st.session_state)
            or st.session_state.get("random_mode")
        )
        if has_search_intent and st.session_state.get("duplicate_mode"):
            clear_duplicate_state()

        if clear_duplicates:
            clear_duplicate_state()
            st.rerun()

        if show_duplicates:
            with st.spinner("Agrupando duplicatas..."):
                groups = find_duplicate_groups(backend, threshold=duplicate_threshold, max_neighbors=duplicate_neighbors)
            st.session_state["duplicate_groups"] = groups
            st.session_state["duplicate_mode"] = True
            visible_groups = filter_duplicate_groups(groups, duplicate_min_group_size)
            visible_groups = sort_duplicate_groups(visible_groups, duplicate_sort_mode)
            if duplicate_view_mode == "Lista unica":
                render_duplicate_flat(visible_groups, backend)
            else:
                render_duplicate_groups(visible_groups, backend)
            return

        if st.session_state.get("duplicate_mode"):
            groups = st.session_state.get("duplicate_groups", [])
            visible_groups = filter_duplicate_groups(groups, duplicate_min_group_size)
            visible_groups = sort_duplicate_groups(visible_groups, duplicate_sort_mode)
            if duplicate_view_mode == "Lista unica":
                render_duplicate_flat(visible_groups, backend)
            else:
                render_duplicate_groups(visible_groups, backend)
            return

        if st.session_state.get("random_mode"):
            st.info("Mostrando memes aleatorios da colecao.")
            cache_key = f"random|{selected_db}|{_options_fingerprint(options)}"
            cached = _get_cached_results(cache_key)
            if cached is not None:
                results = cached
            else:
                results = backend.random_results(top_k)
                _set_cached_results(cache_key, results)
        elif uploaded_file:
            clear_duplicate_state()
            file_hash = md5(uploaded_file.getvalue()).hexdigest()
            cache_key = (
                f"image|{selected_db}|{file_hash}|{_options_fingerprint(options)}|"
                f"group={int(group_image_results)}|gthr={image_group_threshold:.2f}|"
                f"singles={int(image_group_show_singletons)}"
            )
            cached = _get_cached_results(cache_key)
            if cached is not None:
                results = cached
            else:
                with st.spinner("Buscando imagens similares..."):
                    results = backend.search_image(Image.open(uploaded_file), options)
                _set_cached_results(cache_key, results)
        elif "similar_index" in st.session_state:
            clear_duplicate_state()
            st.info("Mostrando resultados similares ao item selecionado.")
            if st.button("Voltar para busca por texto"):
                st.session_state.pop("similar_index", None)
                st.rerun()
            similar_idx = int(st.session_state["similar_index"])
            cache_key = f"similar|{selected_db}|{similar_idx}|{_options_fingerprint(options)}"
            cached = _get_cached_results(cache_key)
            if cached is not None:
                results = cached
            else:
                with st.spinner("Buscando similares..."):
                    results = backend.search_similar(similar_idx, options)
                _set_cached_results(cache_key, results)
        elif query:
            st.session_state["random_mode"] = False
            clear_duplicate_state()
            cache_key = f"text|{selected_db}|{query}|{_options_fingerprint(options)}"
            cached = _get_cached_results(cache_key)
            if cached is not None:
                results = cached
            else:
                with st.spinner("Buscando na sua galeria..."):
                    results = backend.search_text(query, options)
                _set_cached_results(cache_key, results)
        else:
            st.session_state.pop("search_results", None)
            st.session_state.pop("search_results_key", None)
            st.info("Digite uma busca ou envie uma imagem para comecar.")
    except ValueError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Erro durante a busca: {exc}")

    if results:
        if uploaded_file and group_image_results:
            grouped = group_search_results(results, image_group_threshold)
            render_grouped_search_results(grouped, backend, image_group_show_singletons)
        else:
            render_results(results, backend)

    dump_perf()


def _execute_import(
    *, db_path: str, import_folder: str, import_files, import_library: str,
    import_library_root: str, import_recursive: bool, import_batch_size: int,
    import_device: str, model_name: str, import_caption_model: str, import_whisper_model: str,
) -> None:
    sources: list[Path] = []
    temp_upload_dir: Path | None = None
    try:
        if import_folder.strip():
            folder_path = Path(import_folder.strip()).expanduser().resolve()
            if folder_path.exists() and folder_path.is_dir():
                sources.append(folder_path)
            else:
                st.session_state["import_feedback"] = {"ok": False, "message": f"Pasta invalida: {folder_path}"}
                st.rerun()

        if import_files:
            temp_upload_dir = Path(tempfile.mkdtemp(prefix="iris_upload_"))
            for file in import_files:
                (temp_upload_dir / file.name).write_bytes(file.getvalue())
            sources.append(temp_upload_dir)

        if not sources:
            st.session_state["import_feedback"] = {"ok": False, "message": "Nenhuma fonte informada para importacao."}
            st.rerun()

        with st.spinner("Importando e indexando novos arquivos..."):
            run_import_job(
                db_path=Path(db_path).resolve(), sources=sources, recursive=import_recursive,
                library_name=import_library.strip() or DEFAULT_LIBRARY_NAME,
                library_root=import_library_root.strip() or str(DEFAULT_LIBRARY_ROOT),
                batch_size=import_batch_size, device=import_device, model_name=model_name,
                caption_model=import_caption_model.strip() or "none",
                whisper_model=import_whisper_model.strip() or "none",
            )
        st.session_state["import_feedback"] = {"ok": True, "message": "Importacao concluida e indice atualizado."}
        st.cache_resource.clear()
        st.session_state.pop("search_results", None)
        st.session_state.pop("search_results_key", None)
        st.rerun()
    except Exception as exc:
        st.session_state["import_feedback"] = {"ok": False, "message": f"Falha na importacao: {exc}"}
        st.rerun()
    finally:
        if temp_upload_dir and temp_upload_dir.exists():
            shutil.rmtree(temp_upload_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
