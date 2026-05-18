from __future__ import annotations

import base64
import io
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path

import cv2
import numpy as np
import streamlit as st
from PIL import Image

from core.concepts import (
    add_reference,
    create_concept,
    delete_concept,
    delete_reference,
    get_confirmed_meme_ids,
    get_media_concepts,
    get_references,
    list_concepts,
    make_thumbnail,
    set_media_confirmed,
    set_media_rejected,
    update_concept,
)
from core.duplicates import DuplicateGroup, find_duplicate_groups
from core.file_ops import move_to_trash, to_file_uri
from core.indexer import (
    DEFAULT_LIBRARY_NAME,
    DEFAULT_LIBRARY_ROOT,
    IndexerConfig,
    create_faiss_indices,
    process_images,
    resolve_device,
)
from core.search_engine import (
    DEFAULT_MODEL,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    IndexRecord,
    MemeSearchEngine,
    SearchOptions,
    SearchResult,
)

st.set_page_config(page_title="Meme Compass", layout="wide", page_icon="🖼️")

st.markdown(
    """
    <style>
    .stImage {
        border-radius: 8px;
        transition: transform .2s;
    }
    .stImage:hover {
        transform: scale(1.01);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_available_databases(data_dir: str = "data") -> list[str]:
    root = Path(data_dir)
    root.mkdir(exist_ok=True)
    files = sorted(root.rglob("*.db"))
    return sorted([path.relative_to(root).as_posix() for path in files], reverse=True)


@st.cache_resource(show_spinner="Carregando modelo e indice...")
def load_engine(db_path: str, model_name: str, media_root: str) -> MemeSearchEngine:
    return MemeSearchEngine(db_path=db_path, model_name=model_name, media_root=media_root)


def search_mode_options(mode: str, engine: MemeSearchEngine) -> tuple[float, float, float]:
    if mode == "Foco no Texto":
        return 0.0, 3.0, 0.4
    if mode == "Foco Visual":
        return 0.65, 0.5, 0.0
    return (
        float(engine.weights.get("balance", 0.65)),
        float(engine.weights.get("text_bonus", 1.0)),
        float(engine.weights.get("lexical_weight", 0.0)),
    )


def selection_key(index: int) -> str:
    return f"select_{index}"


def selected_record_paths(engine: MemeSearchEngine) -> list[str]:
    selected: list[str] = []
    for key, value in st.session_state.items():
        if not value or not key.startswith("select_"):
            continue
        try:
            index = int(key.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if index < 0 or index >= len(engine.records):
            continue
        path = engine.records[index].resolved_path
        if path and os.path.exists(path):
            selected.append(path)
    return sorted(dict.fromkeys(selected))


def clear_selection_state() -> None:
    for key in list(st.session_state):
        if key.startswith("select_"):
            st.session_state[key] = False


def clear_video_state() -> None:
    for key in list(st.session_state):
        if key.startswith("vid_loaded_"):
            del st.session_state[key]


def _is_blank_frame(frame_bgr: object) -> bool:
    """True se o frame for tudo preto, tudo branco ou cor sólida (sem conteúdo visual)."""
    try:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        mean = float(gray.mean())
        std = float(gray.std())
        return std < 12.0 or mean < 8.0 or mean > 247.0
    except Exception:
        return True


@st.cache_data(max_entries=1000, ttl=3600)
def _video_thumbnail(file_path: str) -> bytes | None:
    """Extrai thumb do vídeo: tenta frame 0 (como o browser fazia), com fallback para 1/4."""
    try:
        cap = cv2.VideoCapture(file_path)
        total = max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 1)

        # Candidatos: frame 0 primeiro (comportamento original do browser), depois frações
        candidates = [0, total // 4, total // 2, total // 8]
        chosen_frame = None
        for pos in candidates:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(pos, 0))
            ok, frame = cap.read()
            if ok and not _is_blank_frame(frame):
                chosen_frame = frame
                break

        cap.release()
        if chosen_frame is None:
            return None

        frame_rgb = cv2.cvtColor(chosen_frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame_rgb)
        pil_img.thumbnail((480, 480))
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
    except Exception:
        return None


@st.cache_data(max_entries=500)
def _thumb_b64(file_path: str, is_video: bool) -> str:
    """Thumbnail 80×80 como base64 JPEG. Cacheado por caminho."""
    try:
        if is_video:
            data = _video_thumbnail(file_path)
            return base64.b64encode(data).decode() if data else ""
        img = Image.open(file_path).convert("RGB")
        img.thumbnail((80, 80))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _safe_render_media(
    file_path: str | None,
    exists: bool,
    ext: str,
    item_key: str,
) -> None:
    """Renderiza imagem ou vídeo com lazy loading e tratamento de erro."""
    if not exists or not file_path:
        st.warning("Arquivo nao encontrado.")
        return
    if ext in VIDEO_EXTENSIONS:
        vid_key = f"vid_loaded_{item_key}"
        if not st.session_state.get(vid_key):
            thumb = _video_thumbnail(file_path)
            if thumb:
                st.image(thumb, use_container_width=True)
            else:
                st.caption("🎬")
            if st.button("▶ Reproduzir", key=f"play_{item_key}"):
                st.session_state[vid_key] = True
                st.rerun()
        else:
            try:
                st.video(file_path)
            except Exception:
                st.warning("Nao foi possivel reproduzir.")
                try:
                    st.link_button("Abrir no player", to_file_uri(file_path))
                except Exception:
                    pass
    else:
        try:
            st.image(file_path, use_container_width=True)
        except Exception:
            st.warning("Nao foi possivel exibir a imagem.")
            try:
                st.link_button("Abrir arquivo", to_file_uri(file_path))
            except Exception:
                pass


def _selected_record_indices(engine: MemeSearchEngine) -> list[int]:
    indices: list[int] = []
    for key, value in st.session_state.items():
        if value and key.startswith("select_"):
            try:
                idx = int(key.split("_", 1)[1])
                if 0 <= idx < len(engine.records):
                    indices.append(idx)
            except (IndexError, ValueError):
                continue
    return indices


def render_floating_selection_panel(engine: MemeSearchEngine) -> None:
    """Painel flutuante fixo no canto inferior-direito (position:fixed via st.markdown)."""
    selected_indices = _selected_record_indices(engine)
    n = len(selected_indices)
    if n == 0:
        return

    thumbs_html = ""
    for idx in selected_indices[:8]:
        record = engine.records[idx]
        fp = record.resolved_path
        ex = bool(fp and os.path.exists(fp))
        ext = os.path.splitext(fp or record.caminho)[1].lower()
        b64 = _thumb_b64(fp, ext in VIDEO_EXTENSIONS) if ex and fp else ""
        # sanitize title (avoid breaking HTML attributes)
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

    # Usamos input[type=checkbox] + label para toggle sem JavaScript
    # O id único por count evita cache do browser manter estado errado entre reruns
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


# Fragment para as associações confirmadas de um conceito (sem rerun da página inteira)
def _concept_associations_body(engine: MemeSearchEngine, concept_id: int) -> None:
    PAGE_SIZE = 30
    page_key = f"_assoc_page_{concept_id}"

    if page_key not in st.session_state:
        st.session_state[page_key] = 1

    conn = sqlite3.connect(engine.db_path)
    try:
        confirmed_ids = get_confirmed_meme_ids(conn, concept_id)
    finally:
        conn.close()

    confirmed_records = [r for r in engine.records if r.db_id in confirmed_ids]

    st.markdown("**Associacoes confirmadas**")
    if not confirmed_records:
        st.caption("Nenhuma associacao confirmada ainda.")
        return

    total = len(confirmed_records)
    visible_count = min(st.session_state[page_key] * PAGE_SIZE, total)
    visible_records = confirmed_records[:visible_count]

    def _ck(db_id: int) -> str:
        return f"ck_assoc_{concept_id}_{db_id}"

    # Read selections directly from Streamlit's own checkbox keys (single source of truth)
    selected_db_ids = [r.db_id for r in visible_records if st.session_state.get(_ck(r.db_id), False)]
    n_sel = len(selected_db_ids)

    # --- action bar ---
    bar_left, bar_right = st.columns([3, 1])
    with bar_left:
        st.caption(f"{total} item(ns) — mostrando {visible_count}")
    with bar_right:
        if n_sel > 0 and st.button(
            f"Remover {n_sel} selecionado(s)",
            key=f"rm_assoc_batch_{concept_id}",
            type="primary",
        ):
            conn2 = sqlite3.connect(engine.db_path)
            try:
                set_media_rejected(conn2, concept_id, selected_db_ids)
            finally:
                conn2.close()
            for db_id in selected_db_ids:
                st.session_state.pop(_ck(db_id), None)
            st.rerun()

    # --- grid ---
    assoc_cols = st.columns(3)
    for pos, record in enumerate(visible_records):
        with assoc_cols[pos % 3]:
            fp = record.resolved_path
            ex = bool(fp and os.path.exists(fp))
            ext = os.path.splitext(fp or record.caminho)[1].lower()
            _safe_render_media(fp, ex, ext, f"cpt_assoc_{concept_id}_{record.db_id}")
            st.caption(record.arquivo)
            st.checkbox("Remover", key=_ck(record.db_id))

    # --- load more ---
    if visible_count < total:
        remaining = total - visible_count
        if st.button(f"Carregar mais ({remaining} restantes)", key=f"assoc_more_{concept_id}"):
            st.session_state[page_key] += 1
            st.rerun()


# Aplica @st.fragment se disponível (Streamlit 1.33+), senão usa função normal
_frag = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
_concept_associations_section = (
    _frag(_concept_associations_body) if _frag else _concept_associations_body
)


def render_sidebar_selection_panel(engine: MemeSearchEngine) -> None:
    """Painel de seleção na sidebar — sempre visível independente do scroll."""
    selected_indices = _selected_record_indices(engine)
    n = len(selected_indices)

    st.sidebar.markdown("### Selecao")
    st.sidebar.caption(f"{n} item(ns) selecionado(s)")

    if n > 0:
        if st.sidebar.button("Limpar selecao", key="sb_clear_sel"):
            clear_selection_state()
            st.rerun()

        if st.sidebar.button(
            "Mover selecionadas para lixeira",
            key="sb_trash_sel",
            help="Move para a lixeira do sistema. Nao usa rm.",
        ):
            trash_selected(engine)

        with st.sidebar.expander(f"Ver {n} item(ns) selecionado(s)"):
            for idx in selected_indices[:12]:
                record = engine.records[idx]
                fp = record.resolved_path
                ex = bool(fp and os.path.exists(fp))
                ext = os.path.splitext(fp or record.caminho)[1].lower()
                if ex and ext not in VIDEO_EXTENSIONS:
                    try:
                        st.sidebar.image(fp, width=120)
                    except Exception:
                        st.sidebar.caption("📷 " + record.arquivo[:20])
                else:
                    thumb = _video_thumbnail(fp) if ex else None
                    if thumb:
                        st.sidebar.image(thumb, width=120)
                    else:
                        st.sidebar.caption(("🎬 " if ext in VIDEO_EXTENSIONS else "❓ ") + record.arquivo[:20])
            if n > 12:
                st.sidebar.caption(f"... e mais {n - 12} item(ns)")

        # Adicionar à coleção
        collections = engine.list_collections()
        if collections:
            col_options = {c["name"]: c["id"] for c in collections}
            target_col = st.sidebar.selectbox(
                "Adicionar a colecao",
                options=["— escolha —"] + list(col_options.keys()),
                key="sidebar_add_to_col",
            )
            if st.sidebar.button("Adicionar selecionadas", key="sb_add_to_col", disabled=target_col == "— escolha —"):
                db_ids = selected_record_db_ids(engine)
                if db_ids and target_col in col_options:
                    added = engine.add_records_to_collection(db_ids, col_options[target_col])
                    st.sidebar.success(f"{added} item(ns) adicionado(s).")
                    st.rerun()
    else:
        st.sidebar.caption("Nenhum item selecionado.")


def render_rejection_summary(engine: MemeSearchEngine, concept_id: int, matches: list[tuple[int, float]]) -> None:
    """Mini-galeria dos itens marcados para rejeição no auto-match."""
    rejected_indices: list[int] = []
    for record_idx, _ in matches:
        rej_key = f"rej_{concept_id}_{record_idx}"
        if st.session_state.get(rej_key, False) and record_idx < len(engine.records):
            rejected_indices.append(record_idx)

    n = len(rejected_indices)
    if n == 0:
        st.caption("Nenhum item marcado para rejeicao.")
        return

    st.caption(f"**{n} item(ns) marcado(s) para rejeicao:**")
    thumb_cols = st.columns(min(n, 6))
    for pos, idx in enumerate(rejected_indices[:6]):
        record = engine.records[idx]
        fp = record.resolved_path
        ex = bool(fp and os.path.exists(fp))
        ext = os.path.splitext(fp or record.caminho)[1].lower()
        with thumb_cols[pos]:
            if ex and ext not in VIDEO_EXTENSIONS:
                try:
                    st.image(fp, width=90)
                except Exception:
                    st.caption("📷")
            else:
                thumb = _video_thumbnail(fp) if ex else None
                if thumb:
                    st.image(thumb, width=90)
                else:
                    st.caption("🎬")
            st.caption(record.arquivo[:14])
    if n > 6:
        st.caption(f"... e mais {n - 6}")


def clear_duplicate_state() -> None:
    st.session_state.pop("duplicate_groups", None)
    st.session_state["duplicate_mode"] = False


def trash_selected(engine: MemeSearchEngine) -> None:
    selected_paths = selected_record_paths(engine)
    moved, failed = move_to_trash(selected_paths)
    st.session_state["trash_feedback"] = {
        "moved": moved,
        "failed": failed,
    }
    clear_selection_state()
    st.cache_resource.clear()
    st.session_state.pop("similar_index", None)
    clear_duplicate_state()
    st.session_state.pop("search_results", None)
    st.session_state.pop("search_results_key", None)
    st.rerun()


def selected_record_db_ids(engine: MemeSearchEngine) -> list[int]:
    db_ids: list[int] = []
    for key, value in st.session_state.items():
        if not value or not key.startswith("select_"):
            continue
        try:
            index = int(key.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if index < 0 or index >= len(engine.records):
            continue
        db_id = engine.records[index].db_id
        if db_id:
            db_ids.append(db_id)
    return sorted(dict.fromkeys(db_ids))


def render_selection_panel(engine: MemeSearchEngine) -> None:
    selected_paths = selected_record_paths(engine)
    st.sidebar.markdown("### Selecao")
    st.sidebar.caption(f"{len(selected_paths)} imagem(ns) selecionada(s)")

    if st.sidebar.button(
        "Mover selecionadas para lixeira",
        disabled=not selected_paths,
        help="Move para a lixeira do sistema. Nao usa rm.",
    ):
        trash_selected(engine)

    collections = engine.list_collections()
    if collections and selected_paths:
        col_options = {c["name"]: c["id"] for c in collections}
        target_col = st.sidebar.selectbox(
            "Adicionar a colecao",
            options=["— escolha —"] + list(col_options.keys()),
            key="sidebar_add_to_col",
        )
        if st.sidebar.button("Adicionar selecionadas", disabled=target_col == "— escolha —"):
            db_ids = selected_record_db_ids(engine)
            if db_ids and target_col in col_options:
                added = engine.add_records_to_collection(db_ids, col_options[target_col])
                st.sidebar.success(f"{added} item(ns) adicionado(s) a '{target_col}'.")
                st.rerun()


def render_inline_selection_panel(engine: MemeSearchEngine) -> None:
    selected_paths = selected_record_paths(engine)
    col_info, col_action = st.columns([4, 1])
    with col_info:
        st.caption(f"Selecionadas: {len(selected_paths)} imagem(ns)")
    with col_action:
        if st.button(
            "Excluir selecionadas",
            key="inline_trash_selected",
            disabled=not selected_paths,
            help="Move para a lixeira do sistema. Nao usa rm.",
        ):
            trash_selected(engine)


def render_trash_feedback() -> None:
    feedback = st.session_state.pop("trash_feedback", None)
    if not feedback:
        return
    moved = feedback.get("moved", [])
    failed = feedback.get("failed", [])
    if moved:
        st.success(f"{len(moved)} arquivo(s) enviado(s) para a lixeira.")
    if failed:
        st.error(f"{len(failed)} arquivo(s) nao puderam ser movidos.")
        with st.expander("Falhas na lixeira"):
            st.json([{"arquivo": path, "erro": error} for path, error in failed])


def render_import_feedback() -> None:
    feedback = st.session_state.pop("import_feedback", None)
    if not feedback:
        return
    if feedback.get("ok"):
        st.success(str(feedback.get("message", "Importacao concluida.")))
    else:
        st.error(str(feedback.get("message", "Importacao falhou.")))


def options_fingerprint(options: SearchOptions) -> str:
    col = ",".join(str(i) for i in sorted(options.collection_ids)) if options.collection_ids else ""
    cpt = ",".join(str(i) for i in sorted(options.concept_ids)) if options.concept_ids else ""
    return (
        f"top_k={options.top_k}|thr={options.threshold:.4f}|bal={options.balance:.4f}|"
        f"tb={options.text_bonus:.4f}|lw={options.lexical_weight:.4f}|tr={int(options.translate)}|"
        f"col={col}|mt={options.media_type}|cpt={cpt}"
    )


def get_cached_results(cache_key: str) -> list[SearchResult] | None:
    if st.session_state.get("search_results_key") != cache_key:
        return None
    cached = st.session_state.get("search_results")
    if not isinstance(cached, list):
        return None
    return cached


def set_cached_results(cache_key: str, results: list[SearchResult]) -> None:
    st.session_state["search_results_key"] = cache_key
    st.session_state["search_results"] = results


def run_import_job(
    *,
    db_path: Path,
    sources: list[Path],
    recursive: bool,
    library_name: str,
    library_root: str,
    batch_size: int,
    device: str,
    model_name: str,
    caption_model: str,
    whisper_model: str,
) -> None:
    for source in sources:
        if not source.exists():
            continue
        config = IndexerConfig(
            media_dir=source,
            db_path=db_path,
            model_name=model_name,
            batch_size=batch_size,
            device=resolve_device(device),
            recursive=recursive,
            limit=None,
            rebuild_faiss_only=False,
            caption_model=caption_model,
            whisper_model=whisper_model,
            sample_manifest=None,
            library_name=library_name,
            library_root=Path(library_root),
            copy_to_library=True,
        )
        process_images(config)
    create_faiss_indices(db_path, model_name)


def render_result(result: SearchResult, engine: MemeSearchEngine) -> None:
    file_path = result.resolved_path
    exists = bool(file_path and os.path.exists(file_path))
    ext = os.path.splitext(file_path or result.caminho)[1].lower()
    _safe_render_media(file_path, exists, ext, f"res_{result.index}")

    st.caption(f"{result.score:.3f} - {result.arquivo}")
    st.checkbox("Selecionar", key=selection_key(result.index))

    with st.expander("Detalhes"):
        st.write(f"**Arquivo:** {result.arquivo}")
        if file_path:
            st.code(file_path, language=None)
        st.code(
            (
                f"Texto extraido: {result.texto_extraido}\n"
                f"Tags: {result.tags}\n"
                f"Descricao IA: {result.descricao_ia}"
            ),
            language=None,
        )
        record = engine.records[result.index] if result.index < len(engine.records) else None
        if record:
            st.write(
                {
                    "style": record.style,
                    "source_work": record.source_work,
                    "context": record.context,
                    "humor": record.humor,
                    "objects": record.objects,
                }
            )
            if record.visual_json:
                try:
                    st.json(record.visual_json)
                except Exception:
                    st.code(record.visual_json, language="json")
        if result.score_details:
            st.json(result.score_details)

        col_folder, col_file, col_similar = st.columns(3)
        folder_path = os.path.dirname(file_path) if exists and file_path else ""
        with col_folder:
            st.link_button(
                "Abrir pasta",
                to_file_uri(folder_path) if folder_path else "#",
                disabled=not folder_path,
            )
        with col_file:
            st.link_button(
                "Abrir arquivo",
                to_file_uri(file_path) if exists and file_path else "#",
                disabled=not exists,
            )
        with col_similar:
            if st.button("Similares", key=f"similar_{result.index}"):
                st.session_state["similar_index"] = result.index
                st.session_state["query"] = ""
                st.session_state["random_mode"] = False
                st.rerun()

        if record and record.db_id:
            _render_result_collections(engine, record.db_id, result.index)
            _render_result_concepts(engine, record.db_id, result.index)


def _render_result_concepts(engine: MemeSearchEngine, db_id: int, record_index: int) -> None:
    if not engine._has_concept_tables():
        return
    conn = sqlite3.connect(engine.db_path)
    try:
        memberships = get_media_concepts(conn, db_id)
        all_concepts = list_concepts(conn)
    finally:
        conn.close()
    if not all_concepts:
        return
    confirmed_ids = {c["id"] for c in memberships if c["confirmed"] == 1}
    st.markdown("**Conceitos:**")
    for c in all_concepts:
        in_concept = c["id"] in confirmed_ids
        label = f"{'[x]' if in_concept else '[ ]'} {c['name']} ({c['category']})"
        if st.button(label, key=f"cpt_toggle_{record_index}_{c['id']}"):
            conn2 = sqlite3.connect(engine.db_path)
            try:
                if in_concept:
                    set_media_rejected(conn2, c["id"], [db_id])
                else:
                    set_media_confirmed(conn2, c["id"], [db_id])
            finally:
                conn2.close()
            st.rerun()


def _wizard_questions(category: str) -> dict[str, str]:
    if category == "pessoa":
        return {
            "Apelidos ou nomes alternativos": "aliases",
            "Em que contexto aparece nos memes?": "context",
        }
    if category == "lugar":
        return {
            "Pais, cidade ou regiao": "location",
            "Nomes alternativos ou abreviacoes": "aliases",
        }
    if category == "personagem":
        return {
            "De qual obra? (anime, serie, filme, jogo...)": "source",
            "Caracteristicas visuais marcantes": "visual",
        }
    if category == "objeto":
        return {
            "O que e este objeto?": "desc",
            "Como aparece nos memes?": "context",
        }
    return {
        "Descricao livre": "desc",
        "Termos extras de busca (separados por virgula)": "extra_terms",
    }


def _wizard_build_fields(category: str, answers: dict[str, str]) -> tuple[str, str]:
    description = " ".join(v.strip() for v in answers.values() if v.strip())
    terms_parts: list[str] = []
    for key in ("aliases", "extra_terms", "source"):
        if key in answers and answers[key].strip():
            terms_parts.append(answers[key].strip())
    return description.strip(), ", ".join(terms_parts)


def render_concepts_wizard(engine: MemeSearchEngine) -> None:
    step = st.session_state.get("cwiz_step", 1)

    if step == 1:
        st.markdown("**Passo 1 de 4 — Nome e Categoria**")
        name = st.text_input("Nome do conceito", key="cwiz_name_input", placeholder="Ex: João Silva, Cristo Redentor, Goku...")
        category = st.radio(
            "Categoria",
            ["pessoa", "lugar", "objeto", "personagem", "animal", "outro"],
            horizontal=True,
            key="cwiz_cat_radio",
        )
        if st.button("Continuar", key="cwiz1_next", type="primary"):
            if name.strip():
                st.session_state["cwiz_step"] = 2
                st.session_state["cwiz_name"] = name.strip()
                st.session_state["cwiz_category"] = category
                st.rerun()
            else:
                st.warning("Informe um nome.")

    elif step == 2:
        name = st.session_state.get("cwiz_name", "")
        category = st.session_state.get("cwiz_category", "outro")
        st.markdown(f"**Passo 2 de 4 — Contexto de '{name}'**")
        questions = _wizard_questions(category)
        answers: dict[str, str] = {}
        for label, key in questions.items():
            answers[key] = st.text_input(label, key=f"cwiz_q_{key}")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Voltar", key="cwiz2_back"):
                st.session_state["cwiz_step"] = 1
                st.rerun()
        with col2:
            if st.button("Continuar", key="cwiz2_next", type="primary"):
                desc, terms = _wizard_build_fields(category, answers)
                st.session_state["cwiz_description"] = desc
                st.session_state["cwiz_search_terms"] = terms
                st.session_state["cwiz_step"] = 3
                st.rerun()

    elif step == 3:
        name = st.session_state.get("cwiz_name", "")
        st.markdown(f"**Passo 3 de 4 — Imagens de referencia de '{name}'**")
        st.caption("Envie imagens que mostram claramente este conceito. Quanto mais variadas, melhor o reconhecimento.")
        files = st.file_uploader(
            "Selecione as imagens de referencia",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key="cwiz_ref_upload",
        )
        if files:
            cols = st.columns(min(len(files), 6))
            for i, f in enumerate(files[:6]):
                with cols[i]:
                    st.image(f, width=80)
            if len(files) > 6:
                st.caption(f"... e mais {len(files) - 6}")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Voltar", key="cwiz3_back"):
                st.session_state["cwiz_step"] = 2
                st.rerun()
        with col2:
            if st.button("Continuar", key="cwiz3_next", type="primary"):
                if not files:
                    st.warning("Adicione pelo menos uma imagem de referencia.")
                else:
                    refs: list[tuple[bytes, bytes, str]] = []
                    with st.spinner("Processando imagens de referencia..."):
                        for f in files:
                            img = Image.open(f).convert("RGB")
                            emb = engine.encode_image(img)
                            thumb = make_thumbnail(img)
                            refs.append((emb[0].tobytes(), thumb, f.name))
                    st.session_state["cwiz_refs"] = refs
                    st.session_state["cwiz_step"] = 4
                    st.rerun()

    elif step == 4:
        name = st.session_state.get("cwiz_name", "")
        category = st.session_state.get("cwiz_category", "outro")
        description = st.session_state.get("cwiz_description", "")
        search_terms = st.session_state.get("cwiz_search_terms", "")
        refs = st.session_state.get("cwiz_refs", [])
        st.markdown("**Passo 4 de 4 — Resumo**")
        st.write(f"**Nome**: {name} ({category})")
        if description:
            st.write(f"**Descricao**: {description}")
        if search_terms:
            st.write(f"**Termos de busca**: {search_terms}")
        st.write(f"**Referencias**: {len(refs)} imagem(ns)")
        if refs:
            thumb_cols = st.columns(min(len(refs), 6))
            for i, (_, thumb, label) in enumerate(refs[:6]):
                with thumb_cols[i]:
                    st.image(thumb, width=70, caption=label[:20])
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Voltar", key="cwiz4_back"):
                st.session_state["cwiz_step"] = 3
                st.rerun()
        with col2:
            if st.button("Criar conceito", key="cwiz4_create", type="primary"):
                try:
                    conn = sqlite3.connect(engine.db_path)
                    try:
                        concept_id = create_concept(conn, name, description, category, search_terms)
                        for emb_bytes, thumb_bytes, label in refs:
                            add_reference(conn, concept_id, emb_bytes, thumb_bytes, label)
                    finally:
                        conn.close()
                    for key in ["cwiz_step", "cwiz_name", "cwiz_category", "cwiz_description", "cwiz_search_terms", "cwiz_refs"]:
                        st.session_state.pop(key, None)
                    st.success(f"Conceito '{name}' criado com {len(refs)} referencia(s).")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao criar conceito: {exc}")


def _render_concept_details(engine: MemeSearchEngine, concept: dict) -> None:
    concept_id = concept["id"]
    conn = sqlite3.connect(engine.db_path)
    try:
        refs = get_references(conn, concept_id)
        confirmed_ids = get_confirmed_meme_ids(conn, concept_id)
    finally:
        conn.close()

    # References grid
    st.markdown("**Imagens de referencia**")
    if refs:
        ref_cols = st.columns(min(len(refs), 6))
        for i, ref in enumerate(refs):
            with ref_cols[i % 6]:
                if ref["thumbnail"]:
                    st.image(ref["thumbnail"], width=80, caption=ref["label"][:16] if ref["label"] else "")
                if st.button("Remover", key=f"del_ref_{ref['id']}"):
                    conn2 = sqlite3.connect(engine.db_path)
                    try:
                        delete_reference(conn2, ref["id"])
                    finally:
                        conn2.close()
                    st.rerun()
    else:
        st.caption("Nenhuma imagem de referencia.")

    new_ref_files = st.file_uploader(
        "Adicionar referencias",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key=f"add_refs_{concept_id}",
    )
    if new_ref_files and st.button("Adicionar", key=f"do_add_refs_{concept_id}"):
        conn3 = sqlite3.connect(engine.db_path)
        try:
            for f in new_ref_files:
                img = Image.open(f).convert("RGB")
                emb = engine.encode_image(img)
                thumb = make_thumbnail(img)
                add_reference(conn3, concept_id, emb[0].tobytes(), thumb, f.name)
        finally:
            conn3.close()
        st.rerun()

    # Edit
    with st.expander("Editar informacoes"):
        new_name = st.text_input("Nome", value=concept["name"], key=f"en_{concept_id}")
        new_desc = st.text_input("Descricao", value=concept["description"], key=f"ed_{concept_id}")
        new_terms = st.text_input("Termos de busca", value=concept["search_terms"], key=f"et_{concept_id}")
        new_thr = st.slider(
            "Score minimo para auto-match (menor = mais inclusivo)",
            0.40, 0.95, float(concept["auto_threshold"]), 0.01,
            key=f"ethr_{concept_id}",
            help="Valores baixos mostram mais candidatos, incluindo caricaturas e versoes estilizadas. 0.65 e o padrao conservador.",
        )
        if st.button("Salvar", key=f"esave_{concept_id}"):
            conn4 = sqlite3.connect(engine.db_path)
            try:
                update_concept(conn4, concept_id, name=new_name.strip(), description=new_desc.strip(),
                               search_terms=new_terms.strip(), auto_threshold=new_thr)
            finally:
                conn4.close()
            st.rerun()

    st.divider()

    # Auto-match
    st.markdown("**Encontrar matches automaticos**")
    st.caption(
        "O sistema e conservador: tudo que aparece aqui e assumido como pertencente ao conceito "
        "por padrao — inclusive caricaturas, versoes estilizadas e variantes. "
        "Marque 'Rejeitar' apenas para o que claramente nao e esse conceito."
    )
    match_key = f"cpt_matches_{concept_id}"
    col_find, col_top_k = st.columns([2, 1])
    with col_find:
        if st.button("Buscar imagens similares", key=f"find_matches_{concept_id}"):
            with st.spinner("Calculando similaridade visual..."):
                top_k_val = st.session_state.get(f"topk_{concept_id}", 80)
                min_score = float(concept.get("auto_threshold", 0.65))
                matches = engine.find_concept_matches(
                    concept_id, top_k=top_k_val, min_score=min_score
                )
            st.session_state[match_key] = matches
            st.rerun()
    with col_top_k:
        st.number_input("Quantidade maxima", 10, 300, 80, key=f"topk_{concept_id}")

    matches = st.session_state.get(match_key, [])
    if matches:
        st.caption(
            f"{len(matches)} candidato(s) encontrado(s). "
            "Marque apenas os que claramente NAO sao este conceito:"
        )
        match_cols = st.columns(3)
        for pos, (record_idx, score) in enumerate(matches):
            if record_idx >= len(engine.records):
                continue
            record = engine.records[record_idx]
            with match_cols[pos % 3]:
                file_path = record.resolved_path
                exists = bool(file_path and os.path.exists(file_path))
                ext = os.path.splitext(file_path or record.caminho)[1].lower()
                _safe_render_media(file_path, exists, ext, f"cpt_match_{concept_id}_{record_idx}")
                already = record.db_id in confirmed_ids
                caption = f"{score:.3f} — {record.arquivo}"
                if already:
                    caption += " [ja assoc.]"
                st.caption(caption)
                st.checkbox("Rejeitar este", key=f"rej_{concept_id}_{record_idx}", value=False)

        st.divider()
        render_rejection_summary(engine, concept_id, matches)

        if st.button("Aplicar selecao", key=f"apply_matches_{concept_id}", type="primary"):
            to_confirm_ids: list[int] = []
            to_reject_ids: list[int] = []
            for record_idx, _ in matches:
                if record_idx >= len(engine.records):
                    continue
                db_id = engine.records[record_idx].db_id
                if not db_id:
                    continue
                rej_key = f"rej_{concept_id}_{record_idx}"
                if st.session_state.get(rej_key, False):
                    to_reject_ids.append(db_id)
                else:
                    to_confirm_ids.append(db_id)
            conn5 = sqlite3.connect(engine.db_path)
            try:
                if to_confirm_ids:
                    set_media_confirmed(conn5, concept_id, to_confirm_ids)
                if to_reject_ids:
                    set_media_rejected(conn5, concept_id, to_reject_ids)
            finally:
                conn5.close()
            st.session_state.pop(match_key, None)
            st.success(f"{len(to_confirm_ids)} confirmado(s), {len(to_reject_ids)} rejeitado(s).")
            st.rerun()

    st.divider()
    _concept_associations_section(engine, concept_id)
    st.divider()

    # Delete concept
    del_key = f"del_cpt_confirm_{concept_id}"
    if not st.session_state.get(del_key):
        if st.button("Excluir conceito", key=f"del_cpt_{concept_id}", type="secondary"):
            st.session_state[del_key] = True
            st.rerun()
    else:
        st.warning(f"Excluir '{concept['name']}' e todas as suas associacoes?")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("Sim, excluir", key=f"del_cpt_yes_{concept_id}"):
                conn7 = sqlite3.connect(engine.db_path)
                try:
                    delete_concept(conn7, concept_id)
                finally:
                    conn7.close()
                st.session_state.pop(del_key, None)
                st.rerun()
        with col_no:
            if st.button("Cancelar", key=f"del_cpt_no_{concept_id}"):
                st.session_state.pop(del_key, None)
                st.rerun()


def render_concepts_tab(engine: MemeSearchEngine) -> None:
    st.subheader("Conceitos Visuais")
    st.caption("Ensine o sistema a reconhecer pessoas, lugares, personagens e objetos especificos.")

    wizard_active = st.session_state.get("cwiz_step", 0) > 0
    with st.expander("+ Criar novo conceito", expanded=wizard_active):
        render_concepts_wizard(engine)

    conn = sqlite3.connect(engine.db_path)
    try:
        all_concepts = list_concepts(conn)
    finally:
        conn.close()

    if not all_concepts:
        st.info("Nenhum conceito criado ainda. Use o formulario acima para criar o primeiro.")
        return

    st.divider()
    for concept in all_concepts:
        header = f"{concept['name']} ({concept['category']}) — {concept['ref_count']} ref(s), {concept['assoc_count']} assoc."
        with st.expander(header, expanded=False):
            _render_concept_details(engine, concept)


def render_folder_browser(prefix: str = "fb") -> str:
    dir_key = f"{prefix}_dir"
    sel_key = f"{prefix}_selected"

    if dir_key not in st.session_state:
        st.session_state[dir_key] = str(Path.home())
    if sel_key not in st.session_state:
        st.session_state[sel_key] = ""

    current = Path(st.session_state[dir_key])
    if not current.exists():
        current = Path.home()
        st.session_state[dir_key] = str(current)

    col_input, col_go = st.columns([5, 1])
    with col_input:
        typed = st.text_input(
            "Colar caminho",
            key=f"{prefix}_typed",
            placeholder="/caminho/da/pasta",
            label_visibility="collapsed",
        )
    with col_go:
        if st.button("Ir", key=f"{prefix}_goto"):
            p = Path(typed.strip()).expanduser() if typed.strip() else None
            if p and p.exists() and p.is_dir():
                st.session_state[dir_key] = str(p)
                st.rerun()
            elif typed.strip():
                st.error("Pasta nao encontrada.")

    st.caption(f"Navegando: `{current}`")

    try:
        subdirs = sorted(
            (d for d in current.iterdir() if d.is_dir() and not d.name.startswith(".")),
            key=lambda d: d.name.lower(),
        )
    except PermissionError:
        subdirs = []
        st.warning("Sem permissao para listar esta pasta.")

    col_up, col_use = st.columns(2)
    with col_up:
        if st.button("Subir", key=f"{prefix}_up", disabled=current.parent == current):
            st.session_state[dir_key] = str(current.parent)
            st.rerun()
    with col_use:
        if st.button("Usar esta pasta", key=f"{prefix}_use", type="primary"):
            st.session_state[sel_key] = str(current)
            st.rerun()

    if subdirs:
        chosen = st.selectbox(
            "Subpastas",
            [d.name for d in subdirs],
            key=f"{prefix}_pick",
            label_visibility="collapsed",
        )
        if st.button("Entrar", key=f"{prefix}_enter"):
            st.session_state[dir_key] = str(current / chosen)
            st.rerun()
    else:
        st.caption("Sem subpastas.")

    selected = st.session_state.get(sel_key, "")
    if selected:
        st.success(f"Selecionada: `{selected}`")
    return selected


def _render_result_collections(engine: MemeSearchEngine, db_id: int, record_index: int) -> None:
    collections = engine.list_collections()
    if not collections:
        return
    memberships = {c["id"] for c in engine.get_record_collections(db_id)}
    st.markdown("**Colecoes:**")
    for col in collections:
        in_col = col["id"] in memberships
        label = f"{'[x]' if in_col else '[ ]'} {col['name']}"
        if st.button(label, key=f"col_toggle_{record_index}_{col['id']}"):
            if in_col:
                engine.remove_records_from_collection([db_id], col["id"])
            else:
                engine.add_records_to_collection([db_id], col["id"])
            st.rerun()


def render_collections_tab(engine: MemeSearchEngine) -> None:
    collections = engine.list_collections()

    st.subheader("Colecoes")
    col_new_name, col_new_btn = st.columns([3, 1])
    with col_new_name:
        new_name = st.text_input("Nome da nova colecao", key="new_collection_name")
    with col_new_btn:
        st.write("")
        st.write("")
        if st.button("Criar", key="create_collection_btn"):
            name = new_name.strip()
            if name:
                try:
                    engine.create_collection(name)
                    st.success(f"Colecao '{name}' criada.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro: {exc}")
            else:
                st.warning("Informe um nome.")

    if not collections:
        st.info("Nenhuma colecao ainda. Crie uma acima.")
        return

    st.divider()

    for col in collections:
        with st.expander(f"{col['name']} ({col['count']} itens)", expanded=False):
            col_rename, col_del = st.columns([3, 1])
            with col_rename:
                new_col_name = st.text_input(
                    "Renomear para", key=f"rename_col_{col['id']}", value=col["name"]
                )
                if st.button("Renomear", key=f"do_rename_{col['id']}"):
                    name = new_col_name.strip()
                    if name and name != col["name"]:
                        try:
                            engine.rename_collection(col["id"], name)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Erro: {exc}")
            with col_del:
                st.write("")
                st.write("")
                if st.button("Excluir", key=f"del_col_{col['id']}", type="secondary"):
                    engine.delete_collection(col["id"])
                    st.rerun()

            if col["count"] == 0:
                st.caption("Colecao vazia.")
                continue

            # show members
            member_db_ids_rows = []
            try:
                import sqlite3 as _sqlite3

                conn = _sqlite3.connect(engine.db_path)
                conn.row_factory = _sqlite3.Row
                rows = conn.execute(
                    "SELECT meme_id FROM media_collections WHERE collection_id = ?", (col["id"],)
                ).fetchall()
                member_db_ids_rows = [r[0] for r in rows]
            except Exception:
                member_db_ids_rows = []
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            db_id_to_record = {r.db_id: r for r in engine.records if r.db_id}
            member_records = [db_id_to_record[mid] for mid in member_db_ids_rows if mid in db_id_to_record]

            if not member_records:
                st.caption("Itens desta colecao nao estao no indice atual.")
                continue

            st.caption(f"{len(member_records)} item(ns) carregado(s) no indice.")
            img_cols = st.columns(4)
            for pos, rec in enumerate(member_records[:20]):
                with img_cols[pos % 4]:
                    fp = rec.resolved_path
                    ex = bool(fp and os.path.exists(fp))
                    ext = os.path.splitext(fp or rec.caminho)[1].lower()
                    _safe_render_media(fp, ex, ext, f"col_mem_{col['id']}_{rec.db_id}")
                    st.caption(rec.arquivo)
                    if st.button("Remover", key=f"rm_from_col_{col['id']}_{rec.db_id}"):
                        engine.remove_records_from_collection([rec.db_id], col["id"])
                        st.rerun()
            if len(member_records) > 20:
                st.caption(f"... e mais {len(member_records) - 20} itens.")


def render_results(results: list[SearchResult], engine: MemeSearchEngine) -> None:
    if not results:
        st.info("Nenhum resultado encontrado com estes filtros.")
        return

    columns = st.columns(3)
    for pos, result in enumerate(results):
        with columns[pos % 3]:
            render_result(result, engine)


def group_search_results(
    results: list[SearchResult],
    similarity_threshold: float,
) -> list[list[SearchResult]]:
    if len(results) <= 1:
        return [results] if results else []

    embeddings: list[np.ndarray] = []
    for result in results:
        vector = np.asarray(result.embedding, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        embeddings.append(vector / norm if norm > 0 else vector)
    matrix = np.stack(embeddings, axis=0)

    parent = list(range(len(results)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    similarity_matrix = matrix @ matrix.T
    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            if float(similarity_matrix[i, j]) >= similarity_threshold:
                union(i, j)

    grouped_positions: dict[int, list[int]] = {}
    for pos in range(len(results)):
        grouped_positions.setdefault(find(pos), []).append(pos)

    ordered_groups = sorted(grouped_positions.values(), key=lambda positions: positions[0])
    return [[results[pos] for pos in positions] for positions in ordered_groups]


def render_grouped_search_results(
    groups: list[list[SearchResult]],
    engine: MemeSearchEngine,
    show_singletons: bool,
) -> None:
    visible_groups = groups if show_singletons else [group for group in groups if len(group) > 1]
    if not visible_groups:
        st.info("Nenhum grupo com 2+ imagens para este limiar.")
        return

    st.caption(
        f"{len(visible_groups)} grupo(s) exibido(s), {sum(len(group) for group in visible_groups)} imagem(ns)."
    )
    for idx, group in enumerate(visible_groups, start=1):
        st.markdown(f"**Grupo {idx}** - {len(group)} imagem(ns)")
        columns = st.columns(3)
        for pos, result in enumerate(group):
            with columns[pos % 3]:
                render_result(result, engine)
        st.divider()


def filter_duplicate_groups(
    groups: list[DuplicateGroup],
    min_group_size: int,
) -> list[DuplicateGroup]:
    return [group for group in groups if len(group.items) >= min_group_size]


def duplicate_item_mtime(item: object) -> float:
    resolved_path = getattr(item, "resolved_path", None)
    if not resolved_path or not os.path.exists(resolved_path):
        return 0.0
    try:
        return float(os.path.getmtime(resolved_path))
    except OSError:
        return 0.0


def format_item_mtime(item: object) -> str:
    ts = duplicate_item_mtime(item)
    if ts <= 0:
        return "data: desconhecida"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("data: %Y-%m-%d %H:%M")


def sort_duplicate_groups(
    groups: list[DuplicateGroup],
    sort_mode: str,
) -> list[DuplicateGroup]:
    normalized: list[DuplicateGroup] = []
    for group in groups:
        if sort_mode == "Data (mais nova)":
            items = sorted(group.items, key=duplicate_item_mtime, reverse=True)
        elif sort_mode == "Data (mais antiga)":
            items = sorted(group.items, key=duplicate_item_mtime)
        else:
            items = list(group.items)

        normalized.append(
            DuplicateGroup(
                group_id=group.group_id,
                kind=group.kind,
                score=group.score,
                items=items,
            )
        )

    if sort_mode == "Data (mais nova)":
        return sorted(
            normalized,
            key=lambda group: max((duplicate_item_mtime(item) for item in group.items), default=0.0),
            reverse=True,
        )
    if sort_mode == "Data (mais antiga)":
        return sorted(
            normalized,
            key=lambda group: min((duplicate_item_mtime(item) for item in group.items), default=0.0),
        )
    return normalized


def render_duplicate_groups(groups: list[DuplicateGroup], engine: MemeSearchEngine) -> None:
    if not groups:
        st.info("Nenhuma duplicata encontrada com estes filtros.")
        return

    st.caption(
        f"{len(groups)} grupo(s), {sum(len(group.items) for group in groups)} imagem(ns) envolvidas"
    )
    with st.form("duplicate_selection_form", clear_on_submit=False):
        for group in groups:
            with st.expander(
                f"Grupo {group.group_id} - {len(group.items)} imagens - score minimo {group.score:.4f}",
                expanded=group.group_id <= 5,
            ):
                columns = st.columns(min(len(group.items), 4))
                for pos, item in enumerate(group.items):
                    with columns[pos % len(columns)]:
                        fp = item.resolved_path
                        ex = bool(fp and os.path.exists(fp))
                        ext = os.path.splitext(fp or "")[1].lower()
                        _safe_render_media(fp, ex, ext, f"dup_{group.group_id}_{item.index}")
                        st.caption(
                            f"{item.score_to_anchor:.4f} - {item.arquivo} ({format_item_mtime(item)})"
                        )
                        st.checkbox("Selecionar", key=selection_key(item.index))
                        col_folder, col_file = st.columns(2)
                        folder_path = (
                            os.path.dirname(item.resolved_path)
                            if item.resolved_path and os.path.exists(item.resolved_path)
                            else ""
                        )
                        with col_folder:
                            st.link_button(
                                "Abrir pasta",
                                to_file_uri(folder_path) if folder_path else "#",
                                disabled=not folder_path,
                            )
                        with col_file:
                            st.link_button(
                                "Abrir arquivo",
                                to_file_uri(item.resolved_path)
                                if item.resolved_path and os.path.exists(item.resolved_path)
                                else "#",
                                disabled=not item.resolved_path or not os.path.exists(item.resolved_path),
                            )
        st.form_submit_button("Aplicar selecao")


def render_duplicate_flat(groups: list[DuplicateGroup], engine: MemeSearchEngine) -> None:
    if not groups:
        st.info("Nenhuma duplicata encontrada com estes filtros.")
        return

    total_items = sum(len(group.items) for group in groups)
    st.caption(f"Mostrando {total_items} imagem(ns) em {len(groups)} grupo(s) de duplicatas")
    with st.form("duplicate_flat_selection_form", clear_on_submit=False):
        for group in groups:
            st.markdown(
                f"**Grupo {group.group_id}** | {len(group.items)} imagens | score minimo {group.score:.4f}"
            )
            columns = st.columns(4)
            for pos, item in enumerate(group.items):
                with columns[pos % len(columns)]:
                    fp = item.resolved_path
                    ex = bool(fp and os.path.exists(fp))
                    ext = os.path.splitext(fp or "")[1].lower()
                    _safe_render_media(fp, ex, ext, f"dupflat_{group.group_id}_{item.index}")
                    st.caption(
                        f"{item.score_to_anchor:.4f} - {item.arquivo} ({format_item_mtime(item)})"
                    )
                    st.checkbox("Selecionar", key=selection_key(item.index))
                    col_folder, col_file = st.columns(2)
                    folder_path = (
                        os.path.dirname(item.resolved_path)
                        if item.resolved_path and os.path.exists(item.resolved_path)
                        else ""
                    )
                    with col_folder:
                        st.link_button(
                            "Abrir pasta",
                            to_file_uri(folder_path) if folder_path else "#",
                            disabled=not folder_path,
                        )
                    with col_file:
                        st.link_button(
                            "Abrir arquivo",
                            to_file_uri(item.resolved_path)
                            if item.resolved_path and os.path.exists(item.resolved_path)
                            else "#",
                            disabled=not item.resolved_path or not os.path.exists(item.resolved_path),
                        )
            st.divider()
        st.form_submit_button("Aplicar selecao")


def _filtered_records(engine: MemeSearchEngine, media_type: str) -> list[IndexRecord]:
    if media_type == "video":
        return [r for r in engine.records if os.path.splitext(r.arquivo)[1].lower() in VIDEO_EXTENSIONS]
    if media_type == "image":
        return [r for r in engine.records if os.path.splitext(r.arquivo)[1].lower() in IMAGE_EXTENSIONS]
    return list(engine.records)


def render_gallery_card(
    record: IndexRecord, engine: MemeSearchEngine, score: float | None = None
) -> None:
    file_path = record.resolved_path
    exists = bool(file_path and os.path.exists(file_path))
    ext = os.path.splitext(file_path or record.caminho)[1].lower()
    _safe_render_media(file_path, exists, ext, f"gal_{record.index}")

    label = f"{score:.3f} — {record.arquivo}" if score is not None else record.arquivo
    st.caption(label)
    st.checkbox("Selecionar", key=selection_key(record.index))

    with st.expander("Detalhes"):
        if file_path:
            st.code(file_path, language=None)
        if record.texto_extraido or record.tags:
            st.code(
                f"Texto: {record.texto_extraido}\nTags: {record.tags}",
                language=None,
            )
        folder_path = os.path.dirname(file_path) if exists and file_path else ""
        col_folder, col_file, col_similar = st.columns(3)
        with col_folder:
            st.link_button(
                "Abrir pasta",
                to_file_uri(folder_path) if folder_path else "#",
                disabled=not folder_path,
            )
        with col_file:
            st.link_button(
                "Abrir arquivo",
                to_file_uri(file_path) if exists and file_path else "#",
                disabled=not exists,
            )
        with col_similar:
            if st.button("Similares", key=f"gal_sim_{record.index}"):
                st.session_state["similar_index"] = record.index
                st.session_state["query"] = ""
                st.session_state["random_mode"] = False
                st.rerun()
        if record.db_id:
            _render_result_collections(engine, record.db_id, record.index)
            _render_result_concepts(engine, record.db_id, record.index)


def render_gallery_tab(engine: MemeSearchEngine, options: SearchOptions) -> None:
    col_q, col_img = st.columns([4, 1])
    with col_q:
        gallery_query = st.text_input(
            "Buscar na galeria",
            placeholder="Deixe vazio para ver tudo",
            key="gallery_query",
        )
    with col_img:
        gallery_img = st.file_uploader(
            "Buscar por imagem",
            type=["png", "jpg", "jpeg", "webp"],
            key="gallery_img",
            label_visibility="collapsed",
        )

    in_search = bool(gallery_query.strip() or gallery_img)

    if in_search:
        with st.spinner("Buscando..."):
            if gallery_img:
                img = Image.open(gallery_img).convert("RGB")
                results = engine.search_image(img, options)
            else:
                results = engine.search_text(gallery_query.strip(), options)

        if not results:
            st.info("Nenhum resultado.")
            return

        st.caption(f"{len(results)} resultado(s)")
        cols = st.columns(3)
        for pos, result in enumerate(results):
            if result.index < len(engine.records):
                with cols[pos % 3]:
                    render_gallery_card(engine.records[result.index], engine, score=result.score)
        return

    # browse mode
    records = _filtered_records(engine, options.media_type)
    if not records:
        st.info("Nenhum item com esse filtro de midia.")
        return

    col_sort, col_dir, col_pp = st.columns([3, 1, 1])
    with col_sort:
        sort_by = st.selectbox(
            "Ordenar por",
            ["Importacao", "Nome", "Data do arquivo", "Tamanho", "Tipo"],
            key="gallery_sort",
        )
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
            int(st.number_input("Pagina", min_value=1, max_value=total_pages, value=1, key="gallery_page"))
            - 1
        )

    page_records = sorted_records[page * per_page : (page + 1) * per_page]
    cols = st.columns(3)
    for pos, record in enumerate(page_records):
        with cols[pos % 3]:
            render_gallery_card(record, engine)


def _backup_size_mb(paths: list[Path]) -> float:
    total = sum(p.stat().st_size for p in paths if p.exists() and p.is_file())
    return total / (1024 * 1024)


def create_backup_zip(data_dir: Path, include_library: bool = True) -> bytes:
    import io
    import json
    import zipfile
    from datetime import datetime as _dt

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(data_dir.glob("*.db")):
            zf.write(f, f"databases/{f.name}")
        for f in sorted(data_dir.glob("*.faiss")):
            zf.write(f, f"databases/{f.name}")
        weights = data_dir / "best_weights.json"
        if weights.exists():
            zf.write(weights, "config/best_weights.json")
        library_included = False
        if include_library:
            lib_root = data_dir / "library"
            if lib_root.exists():
                for f in sorted(lib_root.rglob("*")):
                    if f.is_file():
                        zf.write(f, f"library/{f.relative_to(lib_root)}")
                library_included = True
        manifest = {
            "version": "1.0",
            "created_at": _dt.now().isoformat(),
            "software": "meme-compass",
            "library_included": library_included,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    buf.seek(0)
    return buf.getvalue()


def restore_backup_zip(zip_bytes: bytes, data_dir: Path) -> dict[str, int]:
    import io
    import json
    import zipfile

    counts: dict[str, int] = {"databases": 0, "config": 0, "library": 0}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest = {}
        if "manifest.json" in zf.namelist():
            manifest = json.loads(zf.read("manifest.json"))
        for name in zf.namelist():
            if name == "manifest.json":
                continue
            if name.startswith("databases/"):
                filename = name[len("databases/"):]
                if not filename:
                    continue
                target = data_dir / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                counts["databases"] += 1
            elif name.startswith("config/"):
                filename = name[len("config/"):]
                if not filename:
                    continue
                target = data_dir / filename
                target.write_bytes(zf.read(name))
                counts["config"] += 1
            elif name.startswith("library/") and manifest.get("library_included"):
                rel = name[len("library/"):]
                if not rel:
                    continue
                target = data_dir / "library" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                counts["library"] += 1
    return counts


def render_backup_tab() -> None:
    import json

    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    st.subheader("Exportar backup")
    st.caption(
        "Gera um arquivo .zip com tudo: banco de dados, indices FAISS, conceitos, colecoes, "
        "configuracoes e (opcionalmente) as midias da biblioteca. "
        "Importe no outro computador para migrar completamente."
    )

    dbs = list(data_dir.glob("*.db"))
    faiss_files = list(data_dir.glob("*.faiss"))
    lib_root = data_dir / "library"
    lib_files = list(lib_root.rglob("*")) if lib_root.exists() else []
    lib_files = [f for f in lib_files if f.is_file()]

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.metric("Bancos de dados", len(dbs), f"{_backup_size_mb(dbs):.1f} MB")
    with col_b:
        st.metric("Indices FAISS", len(faiss_files), f"{_backup_size_mb(faiss_files):.1f} MB")
    with col_c:
        st.metric("Midias na biblioteca", len(lib_files), f"{_backup_size_mb(lib_files):.1f} MB")

    include_lib = st.checkbox(
        "Incluir midias da biblioteca",
        value=True,
        help="Desmarque se os arquivos estao em outro disco ou voce so quer migrar os metadados/conceitos.",
    )

    est_mb = _backup_size_mb(dbs) + _backup_size_mb(faiss_files)
    if include_lib:
        est_mb += _backup_size_mb(lib_files)
    st.caption(f"Tamanho estimado do zip: ~{est_mb:.1f} MB (antes da compressao)")

    if st.button("Gerar backup", type="primary", key="gen_backup"):
        with st.spinner("Criando arquivo de backup..."):
            zip_bytes = create_backup_zip(data_dir, include_library=include_lib)
        from datetime import datetime as _dt2
        fname = f"meme_compass_backup_{_dt2.now().strftime('%Y%m%d_%H%M%S')}.zip"
        st.download_button(
            "Baixar backup",
            data=zip_bytes,
            file_name=fname,
            mime="application/zip",
            key="dl_backup",
        )

    st.divider()
    st.subheader("Importar backup")
    st.warning(
        "A importacao substitui os bancos e indices existentes. "
        "Faca um backup antes se quiser preservar o estado atual."
    )
    uploaded_zip = st.file_uploader(
        "Selecione o arquivo .zip de backup",
        type=["zip"],
        key="restore_upload",
    )
    if uploaded_zip:
        import io
        import json
        import zipfile

        zip_bytes = uploaded_zip.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            manifest: dict = {}
            if "manifest.json" in names:
                manifest = json.loads(zf.read("manifest.json"))
        n_db = sum(1 for n in names if n.startswith("databases/") and not n.endswith("/"))
        n_lib = sum(1 for n in names if n.startswith("library/") and not n.endswith("/"))
        n_cfg = sum(1 for n in names if n.startswith("config/") and not n.endswith("/"))
        st.info(
            f"Conteudo do backup: {n_db} arquivo(s) de banco/indice, "
            f"{n_lib} midia(s), {n_cfg} config(s). "
            f"Criado em: {manifest.get('created_at', 'desconhecido')}"
        )
        confirm_key = "restore_confirmed"
        if not st.session_state.get(confirm_key):
            if st.button("Restaurar este backup", type="primary", key="restore_btn"):
                st.session_state[confirm_key] = True
                st.rerun()
        else:
            st.error("Confirmar restauracao? Os dados atuais serao substituidos.")
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("Sim, restaurar agora", key="restore_yes"):
                    with st.spinner("Restaurando..."):
                        counts = restore_backup_zip(zip_bytes, data_dir)
                    st.session_state.pop(confirm_key, None)
                    st.success(
                        f"Restaurado: {counts['databases']} arquivo(s) de banco/indice, "
                        f"{counts['library']} midia(s), {counts['config']} config(s). "
                        "Clique em 'Atualizar dados' na sidebar para recarregar."
                    )
            with col_no:
                if st.button("Cancelar", key="restore_no"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()


def inject_video_volume_sync() -> None:
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
                try {
                    window.parent.document.querySelectorAll('video').forEach(initVideo);
                } catch(e) {}
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


def main() -> None:
    if "duplicate_mode" not in st.session_state:
        st.session_state["duplicate_mode"] = False

    st.sidebar.title("Configuracoes")
    databases = get_available_databases()
    if not databases:
        st.sidebar.error("Nenhum banco encontrado. Rode `python -m core.indexer` primeiro.")
        st.stop()

    selected_db = st.sidebar.selectbox("Banco de dados", databases)
    db_path = os.path.join("data", selected_db)

    # Auto-clear selections and video state when DB changes
    _prev_db = st.session_state.get("_active_db")
    if _prev_db is not None and _prev_db != db_path:
        clear_selection_state()
        clear_video_state()
    st.session_state["_active_db"] = db_path
    media_root = st.sidebar.text_input("Pasta de midias", value="media")
    model_name = st.sidebar.text_input("Modelo CLIP", value=DEFAULT_MODEL)

    try:
        engine = load_engine(db_path, model_name, media_root)
    except Exception as exc:
        st.error(f"Falha ao carregar o indice: {exc}")
        st.stop()

    # Garante que tabelas de conceitos existem em bancos legados
    try:
        from core.concepts import create_concept_tables
        _init_conn = sqlite3.connect(engine.db_path)
        try:
            create_concept_tables(_init_conn)
            _init_conn.commit()
        finally:
            _init_conn.close()
    except Exception:
        pass

    st.sidebar.title("Status")
    st.sidebar.info(f"{len(engine.records)} itens indexados")
    missing = sum(1 for record in engine.records if not record.resolved_path or not os.path.exists(record.resolved_path))
    if missing:
        st.sidebar.warning(f"{missing} arquivos nao encontrados no disco")

    all_collections = engine.list_collections()
    collection_filter_ids: frozenset[int] = frozenset()
    if all_collections:
        st.sidebar.markdown("### Colecao")
        col_names = [c["name"] for c in all_collections]
        selected_col_names = st.sidebar.multiselect(
            "Filtrar por colecao",
            options=col_names,
            default=[],
            help="Deixe vazio para buscar em todos os itens.",
        )
        if selected_col_names:
            name_to_id = {c["name"]: c["id"] for c in all_collections}
            collection_filter_ids = frozenset(
                name_to_id[n] for n in selected_col_names if n in name_to_id
            )

    _conn_cpt = sqlite3.connect(engine.db_path)
    try:
        all_concepts_sidebar = list_concepts(_conn_cpt)
    finally:
        _conn_cpt.close()
    concept_filter_ids: frozenset[int] = frozenset()
    if all_concepts_sidebar:
        st.sidebar.markdown("### Conceito")
        cpt_names = [c["name"] for c in all_concepts_sidebar]
        selected_cpt_names = st.sidebar.multiselect(
            "Filtrar por conceito",
            options=cpt_names,
            default=[],
            help="Mostra apenas midias associadas ao conceito. Busca por texto usa a embedding visual do conceito.",
        )
        if selected_cpt_names:
            cpt_name_to_id = {c["name"]: c["id"] for c in all_concepts_sidebar}
            concept_filter_ids = frozenset(
                cpt_name_to_id[n] for n in selected_cpt_names if n in cpt_name_to_id
            )

    render_sidebar_selection_panel(engine)

    if st.sidebar.checkbox("Estatisticas"):
        exts = Counter(os.path.splitext(record.arquivo)[1].lower() for record in engine.records)
        st.sidebar.bar_chart(exts)

    threshold = st.sidebar.slider("Limiar de similaridade", -1.0, 1.0, 0.15)
    top_k = int(st.sidebar.number_input("Quantidade de resultados", 1, 200, 50))

    st.sidebar.markdown("### Tipo de midia")
    media_type = st.sidebar.radio(
        "Mostrar",
        ["Tudo", "Imagens", "Videos"],
        index=0,
        horizontal=True,
    )
    media_type_value = {"Tudo": "all", "Imagens": "image", "Videos": "video"}[media_type]

    st.sidebar.markdown("### Estrategia")
    mode = st.sidebar.radio(
        "Modo",
        ["Hibrido", "Foco no Texto", "Foco Visual", "Personalizado"],
        index=0,
    )
    if mode == "Personalizado":
        balance = st.sidebar.slider("Visual vs conceitual", 0.0, 1.0, 0.5)
        text_bonus = st.sidebar.slider("Peso do texto exato", 0.0, 4.0, 2.0)
        lexical_weight = st.sidebar.slider("Peso lexical", 0.0, 1.0, 0.25)
    else:
        balance, text_bonus, lexical_weight = search_mode_options(mode, engine)

    options = SearchOptions(
        top_k=top_k,
        threshold=threshold,
        balance=balance,
        text_bonus=text_bonus,
        lexical_weight=lexical_weight,
        translate=True,
        collection_ids=collection_filter_ids,
        concept_ids=concept_filter_ids,
        media_type=media_type_value,
    )

    if st.sidebar.button("Atualizar dados"):
        st.cache_resource.clear()
        clear_selection_state()
        clear_video_state()
        st.session_state.pop("similar_index", None)
        clear_duplicate_state()
        st.session_state.pop("search_results", None)
        st.session_state.pop("search_results_key", None)
        st.rerun()

    if st.sidebar.button("Me surpreenda"):
        st.session_state["random_mode"] = True
        st.session_state.pop("similar_index", None)
        clear_duplicate_state()
        st.session_state.pop("search_results", None)
        st.session_state.pop("search_results_key", None)
        st.rerun()

    st.title("Meme Compass")
    inject_video_volume_sync()
    render_floating_selection_panel(engine)
    render_trash_feedback()
    render_import_feedback()

    if "random_mode" not in st.session_state:
        st.session_state["random_mode"] = False

    tab_text, tab_image, tab_gallery, tab_duplicates, tab_import, tab_collections, tab_concepts, tab_backup = st.tabs(
        ["Busca por texto", "Busca por imagem", "Galeria", "Duplicatas", "Importar", "Colecoes", "Conceitos", "Backup"]
    )
    with tab_text:
        query = st.text_input(
            "Descreva o meme",
            placeholder="Ex: bebe decepcionado com nascimento -gato",
            key="query",
        )
    with tab_image:
        group_image_results = st.checkbox(
            "Agrupar resultados similares",
            value=True,
            help="Organiza os resultados em grupos por similaridade visual.",
        )
        image_group_threshold = st.slider(
            "Limiar do agrupamento (busca por imagem)",
            min_value=0.75,
            max_value=0.99,
            value=0.90,
            step=0.01,
        )
        image_group_show_singletons = st.checkbox(
            "Mostrar grupos de 1 imagem",
            value=False,
        )
        uploaded_file = st.file_uploader(
            "Arraste uma imagem para encontrar similares",
            type=["png", "jpg", "jpeg", "webp"],
        )
        if uploaded_file:
            preview = Image.open(uploaded_file).convert("RGB")
            st.image(preview, caption="Imagem de referencia", width=220)
    with tab_gallery:
        render_gallery_tab(engine, options)
    with tab_duplicates:
        duplicate_threshold = st.slider(
            "Similaridade minima",
            0.90,
            1.0,
            0.985,
            0.001,
            help="Valores maiores mostram so duplicatas muito fortes. 0.985 costuma pegar screenshots quase iguais.",
        )
        duplicate_neighbors = int(
            st.number_input("Vizinhos por imagem", min_value=2, max_value=50, value=12)
        )
        duplicate_min_group_size = int(
            st.number_input("Tamanho minimo do grupo", min_value=2, max_value=500, value=2)
        )
        duplicate_view_mode = st.radio(
            "Visualizacao",
            ["Por grupos", "Lista unica"],
            horizontal=True,
        )
        duplicate_sort_mode = st.selectbox(
            "Ordenacao",
            ["Similaridade", "Data (mais nova)", "Data (mais antiga)"],
            index=0,
            help="A ordenacao por data respeita os grupos de similaridade e ordena itens dentro de cada grupo.",
        )
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
        import_library_root = st.text_input(
            "Raiz das bibliotecas",
            value=str(DEFAULT_LIBRARY_ROOT),
        )
        import_recursive = st.checkbox("Importar subpastas", value=True)
        import_batch_size = int(st.number_input("Batch size", min_value=1, max_value=64, value=8))
        import_device = st.selectbox("Dispositivo", ["auto", "cuda", "mps", "cpu"], index=0)
        import_caption_model = st.text_input(
            "Modelo de caption",
            value="microsoft/Florence-2-large",
            help="Use 'none' para desativar.",
        )
        import_whisper_model = st.text_input(
            "Modelo Whisper",
            value="tiny",
            help="Use 'none' para desativar transcricao de audio.",
        )
        run_import = st.button("Importar e indexar", key="run_import_button")
    with tab_collections:
        render_collections_tab(engine)
    with tab_concepts:
        render_concepts_tab(engine)
    with tab_backup:
        render_backup_tab()

    results: list[SearchResult] = []
    try:
        if run_import:
            sources: list[Path] = []
            temp_upload_dir: Path | None = None
            try:
                if import_folder.strip():
                    folder_path = Path(import_folder.strip()).expanduser().resolve()
                    if folder_path.exists() and folder_path.is_dir():
                        sources.append(folder_path)
                    else:
                        st.session_state["import_feedback"] = {
                            "ok": False,
                            "message": f"Pasta invalida: {folder_path}",
                        }
                        st.rerun()

                if import_files:
                    temp_upload_dir = Path(tempfile.mkdtemp(prefix="meme_compass_upload_"))
                    for file in import_files:
                        (temp_upload_dir / file.name).write_bytes(file.getvalue())
                    sources.append(temp_upload_dir)

                if not sources:
                    st.session_state["import_feedback"] = {
                        "ok": False,
                        "message": "Nenhuma fonte informada para importacao.",
                    }
                    st.rerun()

                with st.spinner("Importando e indexando novos arquivos..."):
                    run_import_job(
                        db_path=Path(db_path).resolve(),
                        sources=sources,
                        recursive=import_recursive,
                        library_name=import_library.strip() or DEFAULT_LIBRARY_NAME,
                        library_root=import_library_root.strip() or str(DEFAULT_LIBRARY_ROOT),
                        batch_size=import_batch_size,
                        device=import_device,
                        model_name=model_name,
                        caption_model=import_caption_model.strip() or "none",
                        whisper_model=import_whisper_model.strip() or "none",
                    )
                st.session_state["import_feedback"] = {
                    "ok": True,
                    "message": "Importacao concluida e indice atualizado.",
                }
                st.cache_resource.clear()
                st.session_state.pop("search_results", None)
                st.session_state.pop("search_results_key", None)
                st.rerun()
            except Exception as exc:
                st.session_state["import_feedback"] = {
                    "ok": False,
                    "message": f"Falha na importacao: {exc}",
                }
                st.rerun()
            finally:
                if temp_upload_dir and temp_upload_dir.exists():
                    shutil.rmtree(temp_upload_dir, ignore_errors=True)

        has_search_intent = bool(
            query
            or uploaded_file
            or ("similar_index" in st.session_state)
            or st.session_state.get("random_mode")
        )
        if has_search_intent and st.session_state.get("duplicate_mode"):
            clear_duplicate_state()

        if clear_duplicates:
            clear_duplicate_state()
            st.rerun()

        if show_duplicates:
            with st.spinner("Agrupando duplicatas e quase duplicatas..."):
                groups = find_duplicate_groups(
                    engine,
                    threshold=duplicate_threshold,
                    max_neighbors=duplicate_neighbors,
                )
            st.session_state["duplicate_groups"] = groups
            st.session_state["duplicate_mode"] = True
            visible_groups = filter_duplicate_groups(groups, duplicate_min_group_size)
            visible_groups = sort_duplicate_groups(visible_groups, duplicate_sort_mode)
            if duplicate_view_mode == "Lista unica":
                render_duplicate_flat(visible_groups, engine)
            else:
                render_duplicate_groups(visible_groups, engine)
            return
        if st.session_state.get("duplicate_mode"):
            groups = st.session_state.get("duplicate_groups", [])
            visible_groups = filter_duplicate_groups(groups, duplicate_min_group_size)
            visible_groups = sort_duplicate_groups(visible_groups, duplicate_sort_mode)
            if duplicate_view_mode == "Lista unica":
                render_duplicate_flat(visible_groups, engine)
            else:
                render_duplicate_groups(visible_groups, engine)
            return
        if st.session_state.get("random_mode"):
            st.info("Mostrando memes aleatorios da colecao.")
            cache_key = f"random|{selected_db}|{options_fingerprint(options)}"
            cached = get_cached_results(cache_key)
            if cached is not None:
                results = cached
            else:
                results = engine.random_results(top_k)
                set_cached_results(cache_key, results)
        elif uploaded_file:
            clear_duplicate_state()
            file_hash = md5(uploaded_file.getvalue()).hexdigest()
            cache_key = (
                f"image|{selected_db}|{file_hash}|{options_fingerprint(options)}|"
                f"group={int(group_image_results)}|gthr={image_group_threshold:.2f}|"
                f"singles={int(image_group_show_singletons)}"
            )
            cached = get_cached_results(cache_key)
            if cached is not None:
                results = cached
            else:
                with st.spinner("Buscando imagens similares..."):
                    results = engine.search_image(Image.open(uploaded_file), options)
                set_cached_results(cache_key, results)
        elif "similar_index" in st.session_state:
            clear_duplicate_state()
            st.info("Mostrando resultados similares ao item selecionado.")
            if st.button("Voltar para busca por texto"):
                st.session_state.pop("similar_index", None)
                st.rerun()
            similar_idx = int(st.session_state["similar_index"])
            cache_key = f"similar|{selected_db}|{similar_idx}|{options_fingerprint(options)}"
            cached = get_cached_results(cache_key)
            if cached is not None:
                results = cached
            else:
                with st.spinner("Buscando similares..."):
                    results = engine.search_similar(similar_idx, options)
                set_cached_results(cache_key, results)
        elif query:
            st.session_state["random_mode"] = False
            clear_duplicate_state()
            cache_key = f"text|{selected_db}|{query}|{options_fingerprint(options)}"
            cached = get_cached_results(cache_key)
            if cached is not None:
                results = cached
            else:
                with st.spinner("Buscando na sua galeria..."):
                    results = engine.search_text(query, options)
                set_cached_results(cache_key, results)
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
            render_grouped_search_results(grouped, engine, image_group_show_singletons)
        else:
            render_results(results, engine)


if __name__ == "__main__":
    main()
