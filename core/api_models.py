"""Modelos de resposta das rotas de leitura da API.

Existem por um bug concreto: o cliente Android declarava ``name`` como string
não-nulável, o servidor devolve ``null`` para pessoa sem nome, e a aba Pessoas
quebrava inteira — sem que nada no servidor documentasse que aquele campo é
nulável. Com os modelos declarados, o FastAPI publica um ``/openapi.json``
preciso e essa divergência passa a ser detectável (e, no limite, os modelos do
cliente passam a ser geráveis a partir do schema) em vez de virar bug de campo.

**Por que ``extra="allow"`` em tudo**: o FastAPI filtra a resposta pelo modelo
declarado. Um campo que exista no retorno mas falte no modelo é descartado em
silêncio — sem erro, sem log — e só aparece como tela quebrada na UI web. Com
``extra="allow"`` o schema documenta os campos conhecidos sem nunca engolir um
campo esquecido. É deliberadamente a opção conservadora: documenta sem poder
causar regressão.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    """Base de toda resposta: nunca descarta campo não declarado."""

    model_config = ConfigDict(extra="allow")


# ── Blocos reutilizados ──────────────────────────────────────────────────────


class MediaPersonRefOut(_Out):
    id: int
    name: str | None = None
    face_id: int | None = None


class MediaConceptRefOut(_Out):
    id: int
    name: str | None = None
    category: str = ""
    confirmed: bool = False


class CollectionRefOut(_Out):
    id: int
    name: str | None = None


class RecordOut(_Out):
    index: int
    db_id: int | None = None
    arquivo: str = ""
    resolved_path: str | None = None
    texto_extraido: str | None = None
    descricao_ia: str | None = None
    tags: str | None = None
    visual_json: str | None = None
    objects: str | None = None
    style: str | None = None
    source_work: str | None = None
    humor: str | None = None
    context: str | None = None
    content_hash: str | None = None
    file_size: int | None = None
    file_mtime: float | None = None
    media_type: str = "image"
    thumbnail_url: str = ""
    thumb_hash: str = ""
    persons: list[MediaPersonRefOut] = []


class RecordDetailOut(RecordOut):
    caminho: str | None = None
    score_details: dict[str, Any] = {}
    collections: list[CollectionRefOut] = []
    concepts: list[MediaConceptRefOut] = []


class SearchResultOut(RecordOut):
    score: float | None = None
    score_details: dict[str, Any] = {}


# ── Respostas de rota ────────────────────────────────────────────────────────


class HealthOut(_Out):
    status: str
    mode: str


class CapabilitiesOut(_Out):
    semantic_search: bool = False
    image_search: bool = False
    face_search: bool = False
    host_administration: bool = False
    folder_import: bool = False
    host_backup: bool = False
    open_host_folder: bool = False
    webchat_enrichment: bool = False


class CurrentUserOut(_Out):
    username: str = ""
    display_name: str = ""
    is_admin: bool = False


class ServerInfoOut(_Out):
    total_records: int = 0
    db_path: str = ""
    media_root: str = ""
    model_name: str = ""
    load_model: bool = False
    multiuser: bool = False
    # Objeto quando há sessão, ausente fora do modo multiusuário. Inferir isso
    # de uma fixture single-user daria `str` e quebraria só com login ativo —
    # foi o que o teste de isolamento entre bibliotecas pegou.
    current_user: CurrentUserOut | None = None
    has_concepts: bool = False
    has_faces: bool = False
    missing_count: int | None = None
    extension_counts: dict[str, int] = {}
    databases: list[str] = []
    capabilities: CapabilitiesOut | None = None


class RecordsPageOut(_Out):
    page: int
    per_page: int
    total: int
    total_pages: int
    missing_count: int = 0
    records: list[RecordOut] = []


class SearchResponseOut(_Out):
    query: str = ""
    total: int = 0
    results: list[SearchResultOut] = []


class SourceSearchResponseOut(SearchResponseOut):
    """Busca a partir de uma mídia/rosto já no catálogo, não de texto."""

    source_index: int | None = None
    source_face: int | None = None
    filename: str | None = None


class PersonOut(_Out):
    id: int
    # Nulável de propósito: é exatamente o campo que quebrava o cliente.
    name: str | None = None
    cover_face_id: int | None = None
    media_count: int = 0
    face_count: int = 0


class PersonsOut(_Out):
    persons: list[PersonOut] = []


class PersonMediaOut(_Out):
    person_id: int
    person_name: str = ""
    total: int = 0
    results: list[SearchResultOut] = []


class FaceOut(_Out):
    id: int
    person_id: int | None = None
    bbox: str = ""
    det_score: float = 0.0
    frame_time: float | None = None


class RecordFacesOut(_Out):
    faces: list[FaceOut] = []


class CollectionOut(_Out):
    id: int
    name: str | None = None
    description: str = ""
    count: int = 0


class CollectionsOut(_Out):
    collections: list[CollectionOut] = []


class CollectionMembersOut(_Out):
    db_ids: list[int] = []
    records: list[RecordOut] = []


class ConceptOut(_Out):
    id: int
    name: str | None = None
    description: str | None = None
    reference_count: int = 0
    match_count: int = 0


class ConceptsOut(_Out):
    concepts: list[ConceptOut] = []


class DbIdsOut(_Out):
    db_ids: list[int] = []


class RecordMetadataOut(_Out):
    # Conteúdo aberto: depende do que EXIF/ffprobe encontrou no arquivo.
    curated: dict[str, Any] = {}
    full: dict[str, Any] = {}
    path_exists: bool = False


class ImportStatusOut(_Out):
    id: str | None = None
    status: str = "idle"
    done: int = 0
    total: int = 0
    imported: int = 0
    quarantined: int = 0
    current: str = ""
    message: str = ""
    started_at: str | None = None
    finished_at: str | None = None


class ImportReviewOut(_Out):
    categories: list[Any] = []
    total: int = 0
    items: list[Any] = []


class ImportSuggestionsOut(_Out):
    job_id: str = ""
    suggestions: list[Any] = []


class BackupConfigOut(_Out):
    backup_dir: str = ""
    backup_auto: bool = True
    backup_keep_last: int = 10
    media_originals_root: str = ""
    dir_ok: bool = False
    warnings: list[Any] = []
    error: str = ""


class OkOut(_Out):
    """Retorno das rotas de mutação: ``ok`` mais o que a operação reportar."""

    ok: bool = True
