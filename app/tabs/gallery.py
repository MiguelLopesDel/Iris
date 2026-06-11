"""Gallery browsing tab — paginated media gallery with sorting and search.

Uses st.fragment to isolate page navigation from the rest of the app,
so clicking ← → only re-runs the gallery, not the sidebar or other tabs.

Performance strategy:
- Image grid: single st.html() with inline base64 thumbnails (1 element, not N widgets)
- Video players: st.video() widgets below the grid (proper playback)
- Pre-buffer: thumbnails for current + adjacent pages warmed before render
"""

from __future__ import annotations

import base64 as b64
import os

import streamlit as st
from PIL import Image

from app.components import _ensure_thumbnail, render_media, selection_key, video_thumbnail
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


# ── Gallery card (used for search results, not browse) ──────────────────────────


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


# ── Thumbnail helpers ───────────────────────────────────────────────────────────


def _thumb_b64(file_path: str) -> str:
    """Read disk-cached 300×300 thumbnail as base64 data URI."""
    try:
        thumb_path = _ensure_thumbnail(file_path)
        with open(thumb_path, "rb") as fh:
            return b64.b64encode(fh.read()).decode()
    except Exception:
        return ""


def _prewarm_thumbnails(records: list[IndexRecord]) -> None:
    """Touch every image thumbnail so _ensure_thumbnail cache is hot before render."""
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


def _placeholder_div(icon: str) -> str:
    """Grey placeholder div for missing files."""
    return (
        f'<div style="aspect-ratio:1;background:#1a1a2e;border-radius:8px;'
        f'display:flex;flex-direction:column;align-items:center;justify-content:center;'
        f'color:#888;font-size:11px;">'
        f'<span style="font-size:28px;">{icon}</span>'
        f'<span>indisponivel</span></div>'
    )


# ── HTML grid renderer ──────────────────────────────────────────────────────────
#
# Key performance insight: each st.image() call creates a Streamlit widget with
# serialization overhead. 24 st.image() ≈ 1400-1600ms.  A single st.html() with
# inline base64 thumbnails sends one HTML string — one element, one message.


def _render_html_grid(records: list[IndexRecord]) -> None:
    """Render all records as a single st.html() grid with inline base64 thumbnails.

    Images → cached 300×300 thumb as data:image/jpeg;base64,...
    Videos → video_thumbnail() as base64 with ▶ overlay
    Missing → placeholder div
    """
    if not records:
        return

    rows: list[str] = []
    for row_start in range(0, len(records), 3):
        cells: list[str] = []
        for record in records[row_start : row_start + 3]:
            fp = record.resolved_path
            ex = bool(fp and os.path.exists(fp))
            ext = os.path.splitext(fp or record.caminho)[1].lower()
            is_vid = ext in VIDEO_EXTENSIONS

            if ex and is_vid:
                thumb_bytes = video_thumbnail(fp)
                if thumb_bytes:
                    vid_b64 = b64.b64encode(thumb_bytes).decode()
                    img_el = (
                        f'<div style="position:relative;">'
                        f'<img src="data:image/jpeg;base64,{vid_b64}" '
                        f'style="width:100%;border-radius:8px;aspect-ratio:1;object-fit:cover;">'
                        f'<div style="position:absolute;inset:0;display:flex;'
                        f'align-items:center;justify-content:center;border-radius:8px;'
                        f'background:rgba(0,0,0,0.25);pointer-events:none;">'
                        f'<span style="font-size:40px;color:#fff;text-shadow:0 0 8px rgba(0,0,0,0.8);'
                        f'opacity:0.9;">▶</span></div></div>'
                    )
                else:
                    img_el = _placeholder_div("🎬")
            elif ex and not is_vid:
                img_b64_str = _thumb_b64(fp)
                if img_b64_str:
                    img_el = (
                        f'<img src="data:image/jpeg;base64,{img_b64_str}" '
                        f'style="width:100%;border-radius:8px;aspect-ratio:1;object-fit:cover;" '
                        f'loading="lazy">'
                    )
                else:
                    img_el = _placeholder_div("🖼️")
            else:
                icon = "🎬" if is_vid else "🖼️"
                img_el = _placeholder_div(icon)

            nome = record.arquivo[:45]
            cells.append(
                f'<div style="display:flex;flex-direction:column;gap:2px;">'
                f'{img_el}'
                f'<div style="font-size:10px;color:#aaa;text-align:center;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'
                f'padding:0 2px;" title="{record.arquivo}">{nome}</div>'
                f'</div>'
            )

        rows.append(
            f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px;">'
            f'{"".join(cells)}</div>'
        )

    st.html(f'<div style="font-family:sans-serif;">{"".join(rows)}</div>')


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
        per_page = st.selectbox(
            "Por pagina", [12, 24, 36, 48, 96], index=1,
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

    page_idx = page - 1
    page_records = sorted_records[page_idx * per_page : (page_idx + 1) * per_page]

    # ── Pre-buffer adjacent pages ──────────────────────────────────────────
    with trace("gallery.prewarm"):
        _prewarm_thumbnails(page_records)
        if page < total_pages:
            _prewarm_thumbnails(
                sorted_records[(page_idx + 1) * per_page : (page_idx + 2) * per_page]
            )
        if page > 1:
            _prewarm_thumbnails(
                sorted_records[(page_idx - 1) * per_page : page_idx * per_page]
            )

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
        disk_info = f" ({total - missing_count} no disco)" if missing_count else ""
        st.caption(f"Pagina {page} de {total_pages} · {total} itens{disk_info}")

    # ── Fast HTML grid (single st.html block, no Streamlit widgets) ────────
    with trace("gallery.render_cards"):
        _render_html_grid(page_records)

    # ── Video players ─────────────────────────────────────────────────────
    video_recs = [
        r for r in page_records
        if os.path.splitext(r.resolved_path or r.caminho)[1].lower() in VIDEO_EXTENSIONS
    ]
    if video_recs:
        with trace("gallery.video_players"):
            # Compact video row: thumbnail + ▶ button per video
            vid_cols = st.columns(min(len(video_recs), 4))
            for i, r in enumerate(video_recs):
                with vid_cols[i % 4]:
                    fp = r.resolved_path
                    vid_key = f"vid_loaded_gal_{r.index}"
                    if st.session_state.get(vid_key):
                        try:
                            st.video(fp)
                        except Exception:
                            st.warning(f"Nao foi possivel reproduzir: {r.arquivo}")
                        if st.button("⏹ Fechar", key=f"close_vid_{r.index}"):
                            del st.session_state[vid_key]
                            st.rerun()
                    else:
                        thumb = video_thumbnail(fp)
                        if thumb:
                            st.image(thumb, use_container_width=True)
                        st.caption(r.arquivo[:35])
                        if st.button("▶ Reproduzir", key=f"play_vid_{r.index}"):
                            st.session_state[vid_key] = True
                            st.rerun()

    # ── Selection & actions row ────────────────────────────────────────────
    with st.expander("Acoes em lote", expanded=False):
        show_selection = st.checkbox("Modo selecao", key="gallery_select_mode")
        if show_selection:
            sel_cols = st.columns(3)
            for i, r in enumerate(page_records):
                with sel_cols[i % 3]:
                    st.checkbox(r.arquivo[:40], key=selection_key(r.index))

            n_selected = sum(
                1 for r in page_records
                if st.session_state.get(selection_key(r.index), False)
            )
            if n_selected:
                st.info(f"{n_selected} item(ns) selecionado(s) nesta pagina")

    # ── Bottom page nav ────────────────────────────────────────────────────
    col_bprev, col_binfo, col_bnext = st.columns([0.5, 5, 0.5])
    with col_bprev:
        if st.button("←", key="gallery_prev2", disabled=(page <= 1), use_container_width=True):
            st.session_state[page_key] -= 1
            st.rerun()
    with col_bnext:
        if st.button("→", key="gallery_next2", disabled=(page >= total_pages), use_container_width=True):
            st.session_state[page_key] += 1
            st.rerun()


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
