"""Collection management tab and inline collection toggles on results."""

from __future__ import annotations

import os

import streamlit as st

from app.components import render_media
from core.backend import SearchBackend


def _render_result_collections(backend: SearchBackend, db_id: int, record_index: int) -> None:
    collections = backend.list_collections()
    if not collections:
        return
    memberships = {c["id"] for c in backend.get_record_collections(db_id)}
    st.markdown("**Colecoes:**")
    for col in collections:
        in_col = col["id"] in memberships
        label = f"{'[x]' if in_col else '[ ]'} {col['name']}"
        if st.button(label, key=f"col_toggle_{record_index}_{col['id']}"):
            if in_col:
                backend.remove_records_from_collection([db_id], col["id"])
            else:
                backend.add_records_to_collection([db_id], col["id"])
            st.rerun()


def render_collections_tab(backend: SearchBackend) -> None:
    collections = backend.list_collections()

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
                    backend.create_collection(name)
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
                new_col_name = st.text_input("Renomear para", key=f"rename_col_{col['id']}", value=col["name"])
                if st.button("Renomear", key=f"do_rename_{col['id']}"):
                    name = new_col_name.strip()
                    if name and name != col["name"]:
                        try:
                            backend.rename_collection(col["id"], name)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Erro: {exc}")
            with col_del:
                st.write("")
                st.write("")
                if st.button("Excluir", key=f"del_col_{col['id']}", type="secondary"):
                    backend.delete_collection(col["id"])
                    st.rerun()

            if col["count"] == 0:
                st.caption("Colecao vazia.")
                continue

            # Show members
            try:
                member_db_ids_rows = backend.get_collection_members(col["id"])
            except Exception:
                member_db_ids_rows = []

            db_id_to_record = {r.db_id: r for r in backend.get_all_records() if r.db_id}
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
                    render_media(fp, ex, ext, f"col_mem_{col['id']}_{rec.db_id}")
                    st.caption(rec.arquivo)
                    if st.button("Remover", key=f"rm_from_col_{col['id']}_{rec.db_id}"):
                        backend.remove_records_from_collection([rec.db_id], col["id"])
                        st.rerun()
            if len(member_records) > 20:
                st.caption(f"... e mais {len(member_records) - 20} itens.")
