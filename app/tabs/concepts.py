"""Concept wizard, auto-match, and concept management tab."""

from __future__ import annotations

import os

import streamlit as st
from PIL import Image

from app.components import render_media, video_thumbnail
from core.backend import SearchBackend
from core.concepts import make_thumbnail


def _render_result_concepts(backend: SearchBackend, db_id: int, record_index: int) -> None:
    """Inline concept toggles on a search result."""
    if not backend.has_concept_tables():
        return
    memberships = backend.get_media_concepts(db_id)
    all_concepts = backend.list_concepts()
    if not all_concepts:
        return
    confirmed_ids = {c["id"] for c in memberships if c["confirmed"] == 1}
    st.markdown("**Conceitos:**")
    for c in all_concepts:
        in_concept = c["id"] in confirmed_ids
        label = f"{'[x]' if in_concept else '[ ]'} {c['name']} ({c['category']})"
        if st.button(label, key=f"cpt_toggle_{record_index}_{c['id']}"):
            if in_concept:
                backend.set_media_rejected(c["id"], [db_id])
            else:
                backend.set_media_confirmed(c["id"], [db_id])
            st.rerun()


# ── Wizard helpers ───────────────────────────────────────────────────────────


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


# ── Wizard ───────────────────────────────────────────────────────────────────


def render_concepts_wizard(backend: SearchBackend) -> None:
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
                            emb = backend.encode_image(img)
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
                    concept_id = backend.create_concept(name, category, description, search_terms)
                    for emb_bytes, thumb_bytes, label in refs:
                        backend.add_reference(concept_id, emb_bytes, thumb_bytes, label)
                    for key in ["cwiz_step", "cwiz_name", "cwiz_category", "cwiz_description", "cwiz_search_terms", "cwiz_refs"]:
                        st.session_state.pop(key, None)
                    st.success(f"Conceito '{name}' criado com {len(refs)} referencia(s).")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao criar conceito: {exc}")


# ── Associations fragment ────────────────────────────────────────────────────


def _concept_associations_body(backend: SearchBackend, concept_id: int) -> None:
    PAGE_SIZE = 30
    page_key = f"_assoc_page_{concept_id}"

    if page_key not in st.session_state:
        st.session_state[page_key] = 1

    confirmed_ids = backend.get_confirmed_meme_ids(concept_id)
    confirmed_records = [r for r in backend.get_all_records() if r.db_id in confirmed_ids]

    st.markdown("**Associacoes confirmadas**")
    if not confirmed_records:
        st.caption("Nenhuma associacao confirmada ainda.")
        return

    total = len(confirmed_records)
    visible_count = min(st.session_state[page_key] * PAGE_SIZE, total)
    visible_records = confirmed_records[:visible_count]

    def _ck(db_id: int) -> str:
        return f"ck_assoc_{concept_id}_{db_id}"

    selected_db_ids = [r.db_id for r in visible_records if st.session_state.get(_ck(r.db_id), False)]
    n_sel = len(selected_db_ids)

    bar_left, bar_right = st.columns([3, 1])
    with bar_left:
        st.caption(f"{total} item(ns) — mostrando {visible_count}")
    with bar_right:
        if n_sel > 0 and st.button(
            f"Remover {n_sel} selecionado(s)",
            key=f"rm_assoc_batch_{concept_id}",
            type="primary",
        ):
            backend.set_media_rejected(concept_id, selected_db_ids)
            for db_id in selected_db_ids:
                st.session_state.pop(_ck(db_id), None)
            st.rerun()

    assoc_cols = st.columns(3)
    for pos, record in enumerate(visible_records):
        with assoc_cols[pos % 3]:
            fp = record.resolved_path
            ex = bool(fp and os.path.exists(fp))
            ext = os.path.splitext(fp or record.caminho)[1].lower()
            render_media(fp, ex, ext, f"cpt_assoc_{concept_id}_{record.db_id}")
            st.caption(record.arquivo)
            st.checkbox("Remover", key=_ck(record.db_id))

    if visible_count < total:
        remaining = total - visible_count
        if st.button(f"Carregar mais ({remaining} restantes)", key=f"assoc_more_{concept_id}"):
            st.session_state[page_key] += 1
            st.rerun()


_frag = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
_concept_associations_section = (
    _frag(_concept_associations_body) if _frag else _concept_associations_body
)


# ── Concept detail view ──────────────────────────────────────────────────────


def render_rejection_summary(backend: SearchBackend, concept_id: int, matches: list[tuple[int, float]]) -> None:
    """Mini-gallery of items marked for rejection in auto-match."""
    rejected_indices: list[int] = []
    for record_idx, _ in matches:
        rej_key = f"rej_{concept_id}_{record_idx}"
        if st.session_state.get(rej_key, False) and record_idx < len(backend.get_all_records()):
            rejected_indices.append(record_idx)

    n = len(rejected_indices)
    if n == 0:
        st.caption("Nenhum item marcado para rejeicao.")
        return

    st.caption(f"**{n} item(ns) marcado(s) para rejeicao:**")
    thumb_cols = st.columns(min(n, 6))
    for pos, idx in enumerate(rejected_indices[:6]):
        record = backend.get_all_records()[idx]
        fp = record.resolved_path
        ex = bool(fp and os.path.exists(fp))
        ext = os.path.splitext(fp or record.caminho)[1].lower()
        with thumb_cols[pos]:
            if ex and ext not in {".mp4", ".webm", ".mkv", ".mov", ".ogg"}:
                try:
                    st.image(fp, width=90)
                except Exception:
                    st.caption("📷")
            else:
                thumb = video_thumbnail(fp) if ex else None
                if thumb:
                    st.image(thumb, width=90)
                else:
                    st.caption("🎬")
            st.caption(record.arquivo[:14])
    if n > 6:
        st.caption(f"... e mais {n - 6}")


def _render_concept_details(backend: SearchBackend, concept: dict) -> None:
    concept_id = concept["id"]
    refs = backend.get_references(concept_id)
    confirmed_ids = backend.get_confirmed_meme_ids(concept_id)

    # References grid
    st.markdown("**Imagens de referencia**")
    if refs:
        ref_cols = st.columns(min(len(refs), 6))
        for i, ref in enumerate(refs):
            with ref_cols[i % 6]:
                if ref["thumbnail"]:
                    st.image(ref["thumbnail"], width=80, caption=ref["label"][:16] if ref["label"] else "")
                if st.button("Remover", key=f"del_ref_{ref['id']}"):
                    backend.delete_reference(ref["id"])
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
        for f in new_ref_files:
            img = Image.open(f).convert("RGB")
            emb = backend.encode_image(img)
            thumb = make_thumbnail(img)
            backend.add_reference(concept_id, emb[0].tobytes(), thumb, f.name)
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
            backend.update_concept(concept_id, name=new_name.strip(), description=new_desc.strip(), search_terms=new_terms.strip(), auto_threshold=new_thr)
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
                matches = backend.find_concept_matches(
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
            if record_idx >= len(backend.get_all_records()):
                continue
            record = backend.get_all_records()[record_idx]
            with match_cols[pos % 3]:
                file_path = record.resolved_path
                exists = bool(file_path and os.path.exists(file_path))
                ext = os.path.splitext(file_path or record.caminho)[1].lower()
                render_media(file_path, exists, ext, f"cpt_match_{concept_id}_{record_idx}")
                already = record.db_id in confirmed_ids
                caption = f"{score:.3f} — {record.arquivo}"
                if already:
                    caption += " [ja assoc.]"
                st.caption(caption)
                st.checkbox("Rejeitar este", key=f"rej_{concept_id}_{record_idx}", value=False)

        st.divider()
        render_rejection_summary(backend, concept_id, matches)

        if st.button("Aplicar selecao", key=f"apply_matches_{concept_id}", type="primary"):
            to_confirm_ids: list[int] = []
            to_reject_ids: list[int] = []
            for record_idx, _ in matches:
                if record_idx >= len(backend.get_all_records()):
                    continue
                db_id = backend.get_all_records()[record_idx].db_id
                if not db_id:
                    continue
                rej_key = f"rej_{concept_id}_{record_idx}"
                if st.session_state.get(rej_key, False):
                    to_reject_ids.append(db_id)
                else:
                    to_confirm_ids.append(db_id)
            if to_confirm_ids:
                backend.set_media_confirmed(concept_id, to_confirm_ids)
            if to_reject_ids:
                backend.set_media_rejected(concept_id, to_reject_ids)
            st.session_state.pop(match_key, None)
            st.success(f"{len(to_confirm_ids)} confirmado(s), {len(to_reject_ids)} rejeitado(s).")
            st.rerun()

    st.divider()
    _concept_associations_section(backend, concept_id)
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
                backend.delete_concept(concept_id)
                st.session_state.pop(del_key, None)
                st.rerun()
        with col_no:
            if st.button("Cancelar", key=f"del_cpt_no_{concept_id}"):
                st.session_state.pop(del_key, None)
                st.rerun()


def render_concepts_tab(backend: SearchBackend) -> None:
    st.subheader("Conceitos Visuais")
    st.caption("Ensine o sistema a reconhecer pessoas, lugares, personagens e objetos especificos.")

    wizard_active = st.session_state.get("cwiz_step", 0) > 0
    with st.expander("+ Criar novo conceito", expanded=wizard_active):
        render_concepts_wizard(backend)

    all_concepts = backend.list_concepts()

    if not all_concepts:
        st.info("Nenhum conceito criado ainda. Use o formulario acima para criar o primeiro.")
        return

    st.divider()
    for concept in all_concepts:
        header = f"{concept['name']} ({concept['category']}) — {concept['ref_count']} ref(s), {concept['assoc_count']} assoc."
        with st.expander(header, expanded=False):
            _render_concept_details(backend, concept)
