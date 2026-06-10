"""Search result rendering — shared by text, image, and similar-item search."""

from __future__ import annotations

import os

import numpy as np
import streamlit as st

from app.components import render_media, selection_key
from core.backend import SearchBackend
from core.file_ops import to_file_uri
from core.search_engine import SearchResult


# ── Result rendering ─────────────────────────────────────────────────────────


def render_result(result: SearchResult, backend: SearchBackend) -> None:
    file_path = result.resolved_path
    exists = bool(file_path and os.path.exists(file_path))
    ext = os.path.splitext(file_path or result.caminho)[1].lower()
    render_media(file_path, exists, ext, f"res_{result.index}")

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
        record = backend.get_all_records()[result.index] if result.index < len(backend.get_all_records()) else None
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
            st.link_button("Abrir pasta", to_file_uri(folder_path) if folder_path else "#", disabled=not folder_path)
        with col_file:
            st.link_button("Abrir arquivo", to_file_uri(file_path) if exists and file_path else "#", disabled=not exists)
        with col_similar:
            if st.button("Similares", key=f"similar_{result.index}"):
                st.session_state["similar_index"] = result.index
                st.session_state["query"] = ""
                st.session_state["random_mode"] = False
                st.rerun()

        if record and record.db_id:
            from app.tabs.collections import _render_result_collections
            from app.tabs.concepts import _render_result_concepts
            _render_result_collections(backend, record.db_id, result.index)
            _render_result_concepts(backend, record.db_id, result.index)


def render_results(results: list[SearchResult], backend: SearchBackend) -> None:
    if not results:
        st.info("Nenhum resultado encontrado com estes filtros.")
        return

    columns = st.columns(3)
    for pos, result in enumerate(results):
        with columns[pos % 3]:
            render_result(result, backend)


# ── Grouping ─────────────────────────────────────────────────────────────────


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

    def _find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def _union(left: int, right: int) -> None:
        root_left = _find(left)
        root_right = _find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    similarity_matrix = matrix @ matrix.T
    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            if float(similarity_matrix[i, j]) >= similarity_threshold:
                _union(i, j)

    grouped_positions: dict[int, list[int]] = {}
    for pos in range(len(results)):
        grouped_positions.setdefault(_find(pos), []).append(pos)

    ordered_groups = sorted(grouped_positions.values(), key=lambda positions: positions[0])
    return [[results[pos] for pos in positions] for positions in ordered_groups]


def render_grouped_search_results(
    groups: list[list[SearchResult]],
    backend: SearchBackend,
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
                render_result(result, backend)
        st.divider()
