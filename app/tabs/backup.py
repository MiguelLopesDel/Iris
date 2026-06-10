"""Backup tab — export and restore the entire Iris database with media."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime as _dt
from pathlib import Path

import streamlit as st


def _backup_size_mb(paths: list[Path]) -> float:
    total = sum(p.stat().st_size for p in paths if p.exists() and p.is_file())
    return total / (1024 * 1024)


def create_backup_zip(data_dir: Path, include_library: bool = True) -> bytes:
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
            "software": "iris",
            "library_included": library_included,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    buf.seek(0)
    return buf.getvalue()


def restore_backup_zip(zip_bytes: bytes, data_dir: Path) -> dict[str, int]:
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
        "Incluir midias da biblioteca", value=True,
        help="Desmarque se os arquivos estao em outro disco ou so quer migrar metadados.",
    )

    est_mb = _backup_size_mb(dbs) + _backup_size_mb(faiss_files)
    if include_lib:
        est_mb += _backup_size_mb(lib_files)
    st.caption(f"Tamanho estimado do zip: ~{est_mb:.1f} MB")

    if st.button("Gerar backup", type="primary", key="gen_backup"):
        with st.spinner("Criando arquivo de backup..."):
            zip_bytes = create_backup_zip(data_dir, include_library=include_lib)
        fname = f"iris_backup_{_dt.now().strftime('%Y%m%d_%H%M%S')}.zip"
        st.download_button("Baixar backup", data=zip_bytes, file_name=fname, mime="application/zip", key="dl_backup")

    st.divider()
    st.subheader("Importar backup")
    st.warning("A importacao substitui os bancos e indices existentes. Faca um backup antes.")

    uploaded_zip = st.file_uploader("Selecione o arquivo .zip de backup", type=["zip"], key="restore_upload")
    if uploaded_zip:
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
