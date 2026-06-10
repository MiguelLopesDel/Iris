"""Gallery browsing tab — paginated media gallery with sorting and search."""

from __future__ import annotations

import os

import streamlit as st

from PIL import Image

from app.components import render_media, selection_key
from core.backend import SearchBackend
from core.file_ops import to_file_uri
from core.search_engine import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, IndexRecord, SearchOptions


def _filtered_records(backend: SearchBackend, options: SearchOptions) -> list[IndexRecord]:
    """Filter records by media type, collection, and concept from SearchOptions."""
    records = backend.get_all_records()

    if options.media_type == "video":
        records = [r for r in records if os.path.splitext(r.arquivo)[1].lower() in VIDEO_EXTENSIONS]
    elif options.media_type == "image":
        records = [r for r in records if os.path.splitext(r.arquivo)[1].lower() in IMAGE_EXTENSIONS]

    if options.collection_ids:
        allowed = backend.get_collection_db_ids(options.collection_ids)
        records = [r for r in records if r.db_id in allowed]

    if options.concept_ids:
        allowed = backend.get_concept_db_ids(options.concept_ids)
        records = [r for r in records if r.db_id in allowed]

    return records


def render_gallery_card(
    record: IndexRecord, backend: SearchBackend, score: float | None = None
) -> None:
    file_path = record.resolved_path
    exists = bool(file_path and os.path.exists(file_path))
    ext = os.path.splitext(file_path or record.caminho)[1].lower()
    render_media(file_path, exists, ext, f"gal_{record.index}")

    label = f"{score:.3f} — {record.arquivo}" if score is not None else record.arquivo
    st.caption(label)
    st.checkbox("Selecionar", key=selection_key(record.index))

    with st.expander("Detalhes"):
        if file_path:
            st.code(file_path, language=None)
        if record.texto_extraido or record.tags:
            st.code(f"Texto: {record.texto_extraido}\nTags: {record.tags}", language=None)
        folder_path = os.path.dirname(file_path) if exists and file_path else ""
        col_folder, col_file, col_similar = st.columns(3)
        with col_folder:
            st.link_button("Abrir pasta", to_file_uri(folder_path) if folder_path else "#", disabled=not folder_path)
        with col_file:
            st.link_button("Abrir arquivo", to_file_uri(file_path) if exists and file_path else "#", disabled=not exists)
        with col_similar:
            if st.button("Similares", key=f"gal_sim_{record.index}"):
                st.session_state["similar_index"] = record.index
                st.session_state["query"] = ""
                st.session_state["random_mode"] = False
                st.rerun()
        if record.db_id:
            from app.tabs.collections import _render_result_collections
            from app.tabs.concepts import _render_result_concepts
            _render_result_collections(backend, record.db_id, record.index)
            _render_result_concepts(backend, record.db_id, record.index)


def render_gallery_tab(backend: SearchBackend, options: SearchOptions) -> None:
    col_q, col_img = st.columns([4, 1])
    with col_q:
        gallery_query = st.text_input("Buscar na galeria", placeholder="Deixe vazio para ver tudo", key="gallery_query")
    with col_img:
        gallery_img = st.file_uploader(
            "Buscar por imagem", type=["png", "jpg", "jpeg", "webp"],
            key="gallery_img", label_visibility="collapsed",
        )

    in_search = bool(gallery_query.strip() or gallery_img)

    if in_search:
        with st.spinner("Buscando..."):
            if gallery_img:
                img = Image.open(gallery_img).convert("RGB")
                results = backend.search_image(img, options)
            else:
                results = backend.search_text(gallery_query.strip(), options)

        if not results:
            st.info("Nenhum resultado.")
            return

        st.caption(f"{len(results)} resultado(s)")
        cols = st.columns(3)
        for pos, result in enumerate(results):
            if result.index < len(backend.get_all_records()):
                with cols[pos % 3]:
                    render_gallery_card(backend.get_all_records()[result.index], backend, score=result.score)
        return

    # Browse mode
    records = _filtered_records(backend, options)
    if not records:
        st.info("Nenhum item com esse filtro de midia.")
        return

    col_sort, col_dir, col_pp = st.columns([3, 1, 1])
    with col_sort:
        sort_by = st.selectbox("Ordenar por", ["Importacao", "Nome", "Data do arquivo", "Tamanho", "Tipo"], key="gallery_sort")
    with col_dir:
        sort_asc = st.checkbox("Crescente", value=False, key="gallery_asc")
    with col_pp:
        per_page = st.selectbox("Por pagina", [24, 48, 96], key="gallery_per_page")

    def _sort_key(r: IndexRecord) -> object:
        if sort_by == "Nome":
            return r.arquivo.lower()
        if sort_by == "Data do arquivo":
            return r.file_mtime or 0.0
        if sort_by == "Tamanho":
            return r.file_size or 0
        if sort_by == "Tipo":
            return os.path.splitext(r.arquivo)[1].lower()
        return r.db_id

    sorted_records = sorted(records, key=_sort_key, reverse=not sort_asc)
    total = len(sorted_records)
    total_pages = max(1, (total + per_page - 1) // per_page)

    col_info, col_page = st.columns([3, 1])
    with col_info:
        st.caption(f"{total} item(ns) — {total_pages} pagina(s)")
    with col_page:
        page = (
            int(st.number_input("Pagina", min_value=1, max_value=total_pages, value=1, key="gallery_page")) - 1
        )

    page_records = sorted_records[page * per_page : (page + 1) * per_page]
    cols = st.columns(3)
    for pos, record in enumerate(page_records):
        with cols[pos % 3]:
            render_gallery_card(record, backend)
