"""Import tab — folder browser and import pipeline."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.indexer import (
    DEFAULT_LIBRARY_NAME,
    DEFAULT_LIBRARY_ROOT,
    IndexerConfig,
    create_faiss_indices,
    process_images,
    resolve_device,
)


def render_folder_browser(prefix: str = "fb") -> str:
    """Interactive folder browser. Returns the selected folder path as a string."""
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
            "Colar caminho", key=f"{prefix}_typed",
            placeholder="/caminho/da/pasta", label_visibility="collapsed",
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
            "Subpastas", [d.name for d in subdirs],
            key=f"{prefix}_pick", label_visibility="collapsed",
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
    """Execute the import indexing pipeline from the given sources."""
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
