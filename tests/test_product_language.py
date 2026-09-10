from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_primary_ui_uses_product_terms_instead_of_internal_terms() -> None:
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert ">Álbuns<" in html
    assert ">Ensinar algo ao Iris<" in html
    assert "Estratégia de busca" not in html
    assert "Nova coleção" not in html
    assert "Novo conceito" not in html


def test_search_uses_qualitative_scope_presets() -> None:
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    for value in ("precise", "balanced", "broad"):
        assert f'value="{value}"' in html
        assert f"{value}: {{" in app_js
    assert 'type="range" id="search-threshold"' not in html
    assert 'type="range" id="search-topk"' not in html


def test_duplicate_and_recognition_controls_hide_raw_scores() -> None:
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    concepts_js = (ROOT / "static" / "concepts.js").read_text(encoding="utf-8")

    assert ">Quase idênticos<" in html
    assert ">Muito parecidos<" in html
    assert ">Explorar parecidos<" in html
    assert "Score mínimo" not in concepts_js
    assert "Auto-match" not in concepts_js
    assert "Itens reconhecidos" in concepts_js
