"""Testes de interação da UI — simulam fluxos completos de utilizador.

Cada teste verifica: "se eu aperto este botão → o backend recebe estas chamadas →
os dados ficam neste estado".

Usam um backend real (LocalBackend) com DB em memória e modelo CLIP desligado
para operações CRUD. Testes que precisam do modelo usam `load_model=True` com CPU.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from core.backend import SearchBackend, create_backend
from core.search_engine import SearchOptions


# ── Helpers ──────────────────────────────────────────────────────────────────

EMB_DIM = 768  # CLIP ViT-L-14 embedding dimension


def _make_test_db(db_path: Path, media_dir: Path) -> None:
    """Cria um DB de teste com 6 registos: imagens, vídeos e um duplicado exato."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS media_libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
            root_path TEXT, created_at TEXT
        );
        INSERT INTO media_libraries (id, name, root_path, created_at)
        VALUES (1, 'default', '{media_dir}', '2026-01-01T00:00:00Z');

        CREATE TABLE IF NOT EXISTS memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, arquivo TEXT UNIQUE, caminho TEXT,
            relative_path TEXT, storage_path TEXT, library_id INTEGER,
            texto_extraido TEXT, descricao_ia TEXT, tags TEXT,
            embedding BLOB, desc_embedding BLOB,
            content_hash TEXT, perceptual_hash TEXT,
            style TEXT, source_work TEXT, humor TEXT, context TEXT,
            visual_json TEXT, objects TEXT,
            file_size INTEGER, file_mtime REAL
        );

        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS media_collections (
            meme_id INTEGER NOT NULL, collection_id INTEGER NOT NULL,
            added_at TEXT,
            PRIMARY KEY (meme_id, collection_id),
            FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE,
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS concepts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'outro',
            search_terms TEXT NOT NULL DEFAULT '',
            auto_threshold REAL NOT NULL DEFAULT 0.65,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS concept_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            concept_id INTEGER NOT NULL,
            embedding BLOB NOT NULL, thumbnail BLOB, label TEXT,
            added_at TEXT NOT NULL,
            FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS concept_media (
            concept_id INTEGER NOT NULL, meme_id INTEGER NOT NULL,
            confirmed INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL,
            PRIMARY KEY (concept_id, meme_id),
            FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
            FOREIGN KEY (meme_id) REFERENCES memes(id) ON DELETE CASCADE
        );
        """
    )

    media_dir.mkdir(parents=True, exist_ok=True)

    # Embeddings reais do CLIP (768-d) para evitar dimension mismatch
    rng = np.random.default_rng(42)
    emb_gato = rng.normal(0, 0.1, EMB_DIM).astype(np.float32)
    emb_gato_var = emb_gato * 0.98 + rng.normal(0, 0.02, EMB_DIM).astype(np.float32)
    emb_cao = rng.normal(0, 0.1, EMB_DIM).astype(np.float32)
    emb_video = rng.normal(0, 0.1, EMB_DIM).astype(np.float32)
    emb_paisagem = -rng.normal(0, 0.1, EMB_DIM).astype(np.float32)

    records = [
        ("gato_bravo.jpg", "gato muito irritado", "angry cat meme", "cat,angry,reaction",
         emb_gato, emb_gato, "hash_gato"),
        ("cachorro_feliz.jpg", "cachorro sorrindo", "happy dog reaction", "dog,happy,reaction",
         emb_cao, emb_cao, "hash_cao"),
        ("gato_variante.jpg", "gato muito bravo close", "angry cat variant", "cat,angry,variant",
         emb_gato_var, emb_gato_var, "hash_gato_var"),
        ("video_meme.mp4", "melhor video", "best video ever", "video,meme,funny",
         emb_video, emb_video, "hash_video"),
        ("paisagem.jpg", "montanha bonita", "beautiful landscape", "nature,landscape",
         emb_paisagem, emb_paisagem, "hash_paisagem"),
        ("duplicado_exato.jpg", "gato muito irritado", "angry cat meme", "cat,angry,reaction",
         emb_gato, emb_gato, "hash_gato"),  # mesmo embedding + mesmo hash que gato_bravo
    ]

    for name, texto, desc, tags, emb, desc_emb, ch in records:
        file_path = media_dir / name
        file_path.touch()
        conn.execute(
            """INSERT INTO memes (arquivo, caminho, relative_path, storage_path, library_id,
               texto_extraido, descricao_ia, tags, embedding, desc_embedding, content_hash)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
            (name, str(file_path), name, name, texto, desc, tags,
             emb.tobytes(), desc_emb.tobytes(), ch),
        )

    conn.commit()
    conn.close()

    _create_minimal_faiss(db_path)


def _create_minimal_faiss(db_path: Path) -> None:
    """Cria índices FAISS a partir dos embeddings no SQLite."""
    import faiss

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT embedding, desc_embedding FROM memes").fetchall()
    conn.close()

    img_vecs = [np.frombuffer(r[0], dtype=np.float32) for r in rows]
    desc_vecs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]

    img_matrix = np.array(img_vecs, dtype=np.float32)
    desc_matrix = np.array(desc_vecs, dtype=np.float32)
    faiss.normalize_L2(img_matrix)
    faiss.normalize_L2(desc_matrix)

    prefix = db_path.with_suffix("")
    for suffix, matrix in [("_image.faiss", img_matrix), ("_desc.faiss", desc_matrix)]:
        idx = faiss.IndexFlatIP(matrix.shape[1])
        idx.add(matrix)
        faiss.write_index(idx, str(prefix.with_name(f"{prefix.name}{suffix}")))


# ── Testes de inicialização ──────────────────────────────────────────────────


class TestBackendInitialization(unittest.TestCase):
    """FLUXO: abrir app → sidebar mostra contagem, estado inicial."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.media = Path(self.tmp.name) / "media"
        self.db_path = Path(self.tmp.name) / "iris.db"
        _make_test_db(self.db_path, self.media)
        self.backend = create_backend(
            db_path=str(self.db_path), media_root=str(self.media), load_model=False,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_total_records_matches_inserted(self) -> None:
        """Ao abrir o app, a sidebar mostra '6 itens indexados'."""
        self.assertEqual(self.backend.get_total_records(), 6)

    def test_all_records_have_existing_files(self) -> None:
        """Cada registo tem um ficheiro real no disco (thumb visível, sem X)."""
        for r in self.backend.get_all_records():
            self.assertTrue(
                r.resolved_path and Path(str(r.resolved_path)).exists(),
                f"Ficheiro ausente: {r.arquivo} → {r.resolved_path}",
            )

    def test_weights_are_loaded(self) -> None:
        """Sidebar 'Estratégia' lê pesos do backend."""
        w = self.backend.weights
        self.assertIn("balance", w)
        self.assertIn("text_bonus", w)
        self.assertIn("lexical_weight", w)

    def test_get_record_by_index(self) -> None:
        """Checkbox 'select_0' → backend.get_record(0) devolve o registo correto."""
        record = self.backend.get_record(0)
        self.assertIsNotNone(record)
        self.assertEqual(record.arquivo, "gato_bravo.jpg")

    def test_get_record_out_of_bounds_returns_none(self) -> None:
        """Índice inválido → None (sem crash)."""
        self.assertIsNone(self.backend.get_record(999))


# ── Testes de coleções ───────────────────────────────────────────────────────


class TestCollectionInteractions(unittest.TestCase):
    """FLUXO: criar coleção → adicionar itens → listar → remover → apagar."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.media = Path(self.tmp.name) / "media"
        self.db_path = Path(self.tmp.name) / "iris.db"
        _make_test_db(self.db_path, self.media)
        self.backend = create_backend(
            db_path=str(self.db_path), media_root=str(self.media), load_model=False,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_and_list_collections(self) -> None:
        """Preencher nome → clicar 'Criar' → coleção aparece na lista."""
        self.backend.create_collection("Favoritos")
        names = [c["name"] for c in self.backend.list_collections()]
        self.assertIn("Favoritos", names)

    def test_add_record_to_collection_and_list_members(self) -> None:
        """Selecionar item → escolher coleção → clicar 'Adicionar' → membro aparece."""
        self.backend.create_collection("Memes Top")
        col_id = self.backend.list_collections()[0]["id"]
        added = self.backend.add_records_to_collection([1], col_id)
        self.assertEqual(added, 1)
        self.assertIn(1, self.backend.get_collection_members(col_id))

    def test_remove_record_from_collection(self) -> None:
        """Clicar 'Remover' num item da coleção → item sai, outros ficam."""
        self.backend.create_collection("Temp")
        col_id = self.backend.list_collections()[0]["id"]
        self.backend.add_records_to_collection([1, 2], col_id)
        self.backend.remove_records_from_collection([1], col_id)
        members = self.backend.get_collection_members(col_id)
        self.assertNotIn(1, members)
        self.assertIn(2, members)

    def test_rename_collection(self) -> None:
        """Editar nome → clicar 'Renomear' → nome atualizado na lista."""
        self.backend.create_collection("Old Name")
        col_id = self.backend.list_collections()[0]["id"]
        self.backend.rename_collection(col_id, "New Name")
        self.assertEqual(self.backend.list_collections()[0]["name"], "New Name")

    def test_delete_collection_removes_it(self) -> None:
        """Clicar 'Excluir' → confirmar → coleção some da lista."""
        self.backend.create_collection("ToDelete")
        col_id = self.backend.list_collections()[0]["id"]
        self.backend.delete_collection(col_id)
        self.assertEqual(len(self.backend.list_collections()), 0)

    def test_get_record_collections_shows_membership(self) -> None:
        """Expandir detalhes do resultado → ver em quais coleções está."""
        self.backend.create_collection("Cats")
        col_id = self.backend.list_collections()[0]["id"]
        self.backend.add_records_to_collection([1], col_id)
        cols = self.backend.get_record_collections(1)
        self.assertEqual(len(cols), 1)
        self.assertEqual(cols[0]["name"], "Cats")

    def test_empty_collection_has_zero_members(self) -> None:
        """Coleção recém-criada → 0 membros."""
        self.backend.create_collection("Vazia")
        col_id = self.backend.list_collections()[0]["id"]
        self.assertEqual(len(self.backend.get_collection_members(col_id)), 0)


# ── Testes de conceitos ──────────────────────────────────────────────────────


class TestConceptInteractions(unittest.TestCase):
    """FLUXO: wizard → criar conceito → referências → auto-match → confirmar/rejeitar."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.media = Path(self.tmp.name) / "media"
        self.db_path = Path(self.tmp.name) / "iris.db"
        _make_test_db(self.db_path, self.media)
        self.backend = create_backend(
            db_path=str(self.db_path), media_root=str(self.media), load_model=False,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_has_concept_tables(self) -> None:
        """Tab 'Conceitos' visível → tabelas existem."""
        self.assertTrue(self.backend.has_concept_tables())

    def test_create_and_list_concepts(self) -> None:
        """Wizard passo 4 → clicar 'Criar conceito' → aparece na lista com contagens."""
        cid = self.backend.create_concept(
            "Gato Bravo", "personagem", "O gato do meme bravo", "gato,bravo,angry"
        )
        self.assertGreater(cid, 0)
        names = [c["name"] for c in self.backend.list_concepts()]
        self.assertIn("Gato Bravo", names)

    def test_add_and_delete_reference(self) -> None:
        """Enviar imagem de referência → salvar → listar → 'Remover' → desaparece."""
        cid = self.backend.create_concept("Teste", "objeto", "Desc", "termos")
        emb = np.random.randn(EMB_DIM).astype(np.float32)

        self.backend.add_reference(cid, emb.tobytes(), b"thumb_data", "ref1.jpg")
        refs = self.backend.get_references(cid)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["label"], "ref1.jpg")

        self.backend.delete_reference(refs[0]["id"])
        self.assertEqual(len(self.backend.get_references(cid)), 0)

    def test_confirm_and_reject_media(self) -> None:
        """Auto-match → marcar uns como confirmados, outros rejeitados → estado persiste."""
        cid = self.backend.create_concept("Teste", "outro", "desc", "termos")

        self.backend.set_media_confirmed(cid, [1])
        self.assertIn(1, self.backend.get_confirmed_meme_ids(cid))

        self.backend.set_media_rejected(cid, [2])
        media = self.backend.get_media_concepts(1)
        self.assertTrue(any(mc["confirmed"] == 1 for mc in media))

    def test_update_concept_fields(self) -> None:
        """Editar nome/descrição → clicar 'Salvar' → valores atualizados."""
        cid = self.backend.create_concept("Old", "outro", "old desc", "old terms")
        self.backend.update_concept(cid, name="New Name", description="nova desc")
        c = next(c for c in self.backend.list_concepts() if c["id"] == cid)
        self.assertEqual(c["name"], "New Name")
        self.assertEqual(c["description"], "nova desc")

    def test_find_concept_matches_without_model(self) -> None:
        """Auto-match sem modelo CLIP → usa FAISS diretamente com embeddings do DB."""
        cid = self.backend.create_concept("Gato", "personagem", "gatos nos memes", "gato,cat")
        emb = np.random.randn(EMB_DIM).astype(np.float32)
        self.backend.add_reference(cid, emb.tobytes(), b"thumb", "ref.jpg")

        matches = self.backend.find_concept_matches(cid, top_k=5, min_score=-1.0)
        # Deve encontrar algo se o índice FAISS foi carregado
        self.assertIsInstance(matches, list)


# ── Testes de duplicatas ─────────────────────────────────────────────────────


class TestDuplicateInteractions(unittest.TestCase):
    """FLUXO: clicar 'Encontrar duplicatas' → grupos → filtrar → ordenar."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.media = Path(self.tmp.name) / "media"
        self.db_path = Path(self.tmp.name) / "iris.db"
        _make_test_db(self.db_path, self.media)
        self.backend = create_backend(
            db_path=str(self.db_path), media_root=str(self.media), load_model=False,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_find_duplicates_via_backend(self) -> None:
        """Clicar 'Encontrar duplicatas' → backend.find_duplicate_groups() devolve grupos."""
        groups = self.backend.find_duplicate_groups(threshold=0.98, max_neighbors=5)
        self.assertGreater(len(groups), 0, "Deveria encontrar pelo menos um grupo de duplicatas")

    def test_exact_hash_duplicates_grouped(self) -> None:
        """gato_bravo.jpg e duplicado_exato.jpg (mesmo hash) → mesmo grupo."""
        groups = self.backend.find_duplicate_groups(threshold=0.98, max_neighbors=5)
        for g in groups:
            files = [item.arquivo for item in g.items]
            if "gato_bravo.jpg" in files and "duplicado_exato.jpg" in files:
                return
        self.fail("gato_bravo.jpg e duplicado_exato.jpg deviam estar no mesmo grupo (hash igual)")

    def test_near_duplicates_grouped_at_low_threshold(self) -> None:
        """gato_variante (98% similar) + threshold 0.97 → mesmo grupo."""
        groups = self.backend.find_duplicate_groups(threshold=0.97, max_neighbors=5)
        for g in groups:
            files = [item.arquivo for item in g.items]
            if "gato_bravo.jpg" in files and "gato_variante.jpg" in files:
                return
        self.fail("gato_bravo e gato_variante deviam estar no mesmo grupo (98% similar)")

    def test_duplicate_items_have_valid_paths(self) -> None:
        """Todos os itens nos grupos de duplicatas têm ficheiros acessíveis."""
        groups = self.backend.find_duplicate_groups(threshold=0.98, max_neighbors=5)
        for g in groups:
            for item in g.items:
                self.assertTrue(
                    item.resolved_path and Path(str(item.resolved_path)).exists(),
                    f"Item {item.arquivo} sem ficheiro em {item.resolved_path}",
                )


# ── Testes de fluxo completo ─────────────────────────────────────────────────


class TestFullInteractionFlows(unittest.TestCase):
    """Fluxos completos: busca → seleciona → coleção → confirma."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.media = Path(self.tmp.name) / "media"
        self.db_path = Path(self.tmp.name) / "iris.db"
        _make_test_db(self.db_path, self.media)
        self.backend = create_backend(
            db_path=str(self.db_path), media_root=str(self.media), load_model=False,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_search_select_add_to_collection_flow(self) -> None:
        """FLUXO COMPLETO:
        1. Utilizador pesquisa → obtém resultados
        2. Seleciona 2 resultados (session_state['select_0'] = True, etc.)
        3. Cria coleção 'Gatos'
        4. Adiciona os selecionados
        5. Verifica que estão na coleção
        """
        # Step 1: Obter registos
        records = self.backend.get_all_records()
        self.assertGreaterEqual(len(records), 3)

        # Step 2: Simular seleção (2 primeiros registos)
        selected_indices = [0, 2]  # gato_bravo + gato_variante
        selected_db_ids = [
            records[idx].db_id for idx in selected_indices if records[idx].db_id
        ]
        self.assertEqual(len(selected_db_ids), 2)

        # Step 3: Criar coleção
        self.backend.create_collection("Gatos")
        col_id = self.backend.list_collections()[0]["id"]

        # Step 4: Adicionar selecionados
        added = self.backend.add_records_to_collection(selected_db_ids, col_id)
        self.assertEqual(added, 2)

        # Step 5: Verificar
        members = self.backend.get_collection_members(col_id)
        for db_id in selected_db_ids:
            self.assertIn(db_id, members)

    def test_concept_create_reference_and_get_matches(self) -> None:
        """FLUXO: criar conceito → adicionar ref → ver matches → confirmar."""
        cid = self.backend.create_concept("Gato", "personagem", "gatos", "gato,cat")
        emb = np.random.randn(EMB_DIM).astype(np.float32)
        self.backend.add_reference(cid, emb.tobytes(), b"thumb", "ref.jpg")

        matches = self.backend.find_concept_matches(cid, top_k=5, min_score=-1.0)
        # Mesmo sem modelo CLIP, deve retornar lista
        self.assertIsNotNone(matches)

        # Confirmar o primeiro se houver matches
        if matches:
            idx, score = matches[0]
            rec = self.backend.get_all_records()[idx]
            if rec.db_id:
                self.backend.set_media_confirmed(cid, [rec.db_id])
                self.assertIn(rec.db_id, self.backend.get_confirmed_meme_ids(cid))

    def test_delete_concept_cleans_up(self) -> None:
        """FLUXO: criar conceito → adicionar ref → apagar → lista vazia."""
        cid = self.backend.create_concept("Temp", "outro", "desc", "terms")
        emb = np.random.randn(EMB_DIM).astype(np.float32)
        self.backend.add_reference(cid, emb.tobytes(), b"thumb", "ref.jpg")
        self.assertEqual(len(self.backend.list_concepts()), 1)

        self.backend.delete_concept(cid)
        self.assertEqual(len(self.backend.list_concepts()), 0)


# ── Testes de busca com modelo ───────────────────────────────────────────────


@unittest.skipUnless(
    os.environ.get("IRIS_RUN_MODEL_TESTS") == "1",
    "Defina IRIS_RUN_MODEL_TESTS=1 para executar testes que carregam o CLIP",
)
class TestSearchWithModel(unittest.TestCase):
    """Testes de busca que precisam do modelo CLIP carregado (CPU)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.media = Path(self.tmp.name) / "media"
        self.db_path = Path(self.tmp.name) / "iris.db"
        _make_test_db(self.db_path, self.media)
        self.backend = create_backend(
            db_path=str(self.db_path), media_root=str(self.media),
            load_model=True, device="cpu",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_search_text_returns_results(self) -> None:
        """Digitar 'gato bravo' → resultados com scores."""
        options = SearchOptions(top_k=10, threshold=-1.0, balance=0.5, text_bonus=2.0)
        results = self.backend.search_text("gato bravo", options)
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertIsInstance(r.score, float)

    def test_search_image_returns_results(self) -> None:
        """Upload de imagem → busca visual devolve resultados."""
        options = SearchOptions(top_k=5, threshold=-1.0, balance=0.65)
        img = Image.new("RGB", (224, 224), color=(255, 100, 100))
        results = self.backend.search_image(img, options)
        self.assertGreater(len(results), 0)

    def test_random_results_respects_count(self) -> None:
        """Clicar 'Me surpreenda' → N resultados aleatórios."""
        results = self.backend.random_results(3)
        self.assertEqual(len(results), 3)

    def test_search_similar_finds_variants(self) -> None:
        """Clicar 'Similares' → devolve vizinhos visuais."""
        options = SearchOptions(top_k=5, threshold=-1.0, balance=0.65)
        results = self.backend.search_similar(0, options)
        filenames = [r.arquivo for r in results]
        self.assertIn("gato_variante.jpg", filenames)


if __name__ == "__main__":
    unittest.main()
