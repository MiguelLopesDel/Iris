"""Gallery browsing tab — paginated media gallery with sorting and search.

Uses st.fragment to isolate page navigation from the rest of the app,
so clicking ← → only re-runs the gallery, not the sidebar or other tabs.
"""

from __future__ import annotations

import os

import streamlit as st
from PIL import Image

from app.components import _ensure_thumbnail, render_media, selection_key
from core.backend import SearchBackend
from core.file_ops import to_file_uri
from core.perf import trace
from core.search_engine import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, IndexRecord, SearchOptions

_frag = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)


# ── Record filtering ────────────────────────────────────────────────────────────


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


# ── Gallery card ────────────────────────────────────────────────────────────────


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
            _render_result_concepts(backend, record.db_id, record.index)
            _render_result_collections(backend, record.db_id, record.index)


# ── Pre-warming ─────────────────────────────────────────────────────────────────
#
# Thumbnails are generated upfront (before rendering) so the grid never blocks
# on disk I/O. Adjacent pages are also pre-warmed so arrow clicks hit warm cache.


def _prewarm_thumbnails(records: list[IndexRecord]) -> None:
    """Touch every image thumbnail so _ensure_thumbnail cache is hot."""
    for r in records:
        fp = r.resolved_path
        if not fp or not os.path.exists(fp):
            continue
        ext = os.path.splitext(fp)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            try:
                _ensure_thumbnail(fp)
            except Exception:
                pass


# ── Browse fragment ─────────────────────────────────────────────────────────────


def _render_browse(backend: SearchBackend, options: SearchOptions) -> None:
    """Paginated gallery grid. Wrapped in st.fragment so page nav is instant."""

    # ── Controls ────────────────────────────────────────────────────────────
    col_sort, col_dir, col_pp = st.columns([3, 1, 1])
    with col_sort:
        sort_by = st.selectbox(
            "Ordenar por", ["Importacao", "Nome", "Data do arquivo", "Tamanho", "Tipo"],
            key="gallery_sort",
        )
    with col_dir:
        sort_asc = st.checkbox("Crescente", value=False, key="gallery_asc")
    with col_pp:
        per_page = st.number_input(
            "Por pagina", min_value=12, max_value=500, value=24, step=12,
            key="gallery_per_page",
        )

    # ── Data (cached — only recomputed when filters/sort change) ────────────
    cache_key = (
        f"{options.media_type}|{frozenset(options.collection_ids)}|{frozenset(options.concept_ids)}"
        f"|{sort_by}|{int(sort_asc)}"
    )
    cached = st.session_state.get("_gallery_cache")
    if cached and cached.get("key") == cache_key:
        sorted_records = cached["records"]
        missing_count = cached["missing"]
    else:
        with trace("gallery.filter_and_sort"):
            records = _filtered_records(backend, options)
        if not records:
            st.info("Nenhum item com esse filtro de midia.")
            st.session_state.pop("_gallery_cache", None)
            return

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

        with trace("gallery.sort"):
            sorted_records = sorted(records, key=_sort_key, reverse=not sort_asc)
        missing_count = sum(
            1 for r in sorted_records
            if not r.resolved_path or not os.path.exists(r.resolved_path)
        )
        st.session_state["_gallery_cache"] = {
            "key": cache_key, "records": sorted_records, "missing": missing_count,
        }

    total = len(sorted_records)
    total_pages = max(1, (total + per_page - 1) // per_page)

    # ── Page navigation ─────────────────────────────────────────────────────
    page_key = "gallery_page"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1

    page = st.session_state[page_key]
    if page > total_pages:
        page = total_pages
        st.session_state[page_key] = page

    col_prev, col_info, col_next = st.columns([0.5, 5, 0.5])
    with col_prev:
        if st.button("←", key="gallery_prev", disabled=(page <= 1), use_container_width=True):
            st.session_state[page_key] -= 1
            st.rerun()
    with col_next:
        if st.button("→", key="gallery_next", disabled=(page >= total_pages), use_container_width=True):
            st.session_state[page_key] += 1
            st.rerun()
    with col_info:
        if missing_count:
            st.caption(
                f"Pagina {page} de {total_pages} · {total} itens no indice "
                f"({total - missing_count} disponiveis)"
            )
        else:
            st.caption(f"Pagina {page} de {total_pages} · {total} itens")

    page_idx = page - 1
    page_records = sorted_records[page_idx * per_page : (page_idx + 1) * per_page]

    # ── Pre-buffer adjacent pages ──────────────────────────────────────────
    with trace("gallery.prewarm"):
        _prewarm_thumbnails(page_records)
        if page < total_pages:
            next_records = sorted_records[(page_idx + 1) * per_page : (page_idx + 2) * per_page]
            _prewarm_thumbnails(next_records)
        if page > 1:
            prev_records = sorted_records[(page_idx - 1) * per_page : page_idx * per_page]
            _prewarm_thumbnails(prev_records)

    # ── Uniform 3-column grid ──────────────────────────────────────────────
    with trace("gallery.render_cards"):
        for row_start in range(0, len(page_records), 3):
            row_records = page_records[row_start : row_start + 3]
            row_cols = st.columns(3)
            for col_idx, record in enumerate(row_records):
                with row_cols[col_idx]:
                    render_gallery_card(record, backend)


# Decorate with fragment so page nav buttons only re-run this function,
# not the entire app (sidebar, all tabs, DB queries).
if _frag:
    _render_browse = _frag(_render_browse)


# ── Main tab entry point ────────────────────────────────────────────────────────


def render_gallery_tab(backend: SearchBackend, options: SearchOptions) -> None:
    col_q, col_img = st.columns([4, 1])
    with col_q:
        gallery_query = st.text_input(
            "Buscar na galeria", placeholder="Deixe vazio para ver tudo",
            key="gallery_query",
        )
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
        for row_start in range(0, len(results), 3):
            row_results = results[row_start : row_start + 3]
            row_cols = st.columns(3)
            for col_idx, result in enumerate(row_results):
                if result.index < len(backend.get_all_records()):
                    with row_cols[col_idx]:
                        render_gallery_card(
                            backend.get_all_records()[result.index], backend,
                            score=result.score,
                        )
        return

    # Browse mode — isolated in fragment for fast page navigation
    _render_browse(backend, options)
