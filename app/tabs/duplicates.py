"""Duplicate detection and management tab."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

from app.components import render_media, selection_key
from core.backend import SearchBackend
from core.duplicates import DuplicateGroup, find_duplicate_groups
from core.file_ops import to_file_uri


def clear_duplicate_state() -> None:
    st.session_state.pop("duplicate_groups", None)
    st.session_state["duplicate_mode"] = False


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


def render_duplicate_groups(groups: list[DuplicateGroup], backend: SearchBackend) -> None:
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
                        render_media(fp, ex, ext, f"dup_{group.group_id}_{item.index}")
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


def render_duplicate_flat(groups: list[DuplicateGroup], backend: SearchBackend) -> None:
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
                    render_media(fp, ex, ext, f"dupflat_{group.group_id}_{item.index}")
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
