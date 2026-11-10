import runpy
import sys

import pytest

from app.text import pdf
from conftest import make_pdf


def test_clean_block_fixes_ligatures_hyphenation_and_spaces():
    raw = "ﬁm­ da pala-\nvra\n  outra\t coisa é "
    assert pdf._clean_block(raw) == "fim da palavra outra coisa é"


def test_signature_ignores_numbers_and_case():
    assert pdf._signature("Capítulo 12") == pdf._signature("CAPÍTULO 7") == "capítulo #"


def test_remove_headers_drops_repeated_edges_and_page_numbers():
    pages = [["Meu Livro", f"Corpo único {name}.", "", f"Página {i}", str(i)]
             for i, name in enumerate(["alfa", "beta", "gama", "delta"])]
    long_block = "x" * 120
    pages[0].insert(1, long_block)
    out = pdf.remove_headers(pages)
    assert out[1] == ["Corpo único beta."]
    assert out[0] == [long_block, "Corpo único alfa."]


def test_remove_headers_keeps_text_repeated_in_few_pages():
    pages = [["Título", "Texto um."], ["Título", "Texto dois."]]
    assert pdf.remove_headers(pages) == pages


def test_merge_paragraphs_joins_continuations():
    blocks = ["Uma frase que", "continua aqui.", "Nova frase", "Começa maiúscula.", "pala-", "vra final."]
    assert pdf.merge_paragraphs(blocks) == [
        "Uma frase que continua aqui.", "Nova frase", "Começa maiúscula.", "palavra final.",
    ]


def test_extract_blocks_reports_progress(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf([[f"Texto da página {i}."] for i in range(12)]))
    calls = []
    pages = pdf.extract_blocks(str(path), lambda i, n: calls.append((i, n)))
    assert len(pages) == 12
    assert pages[3] == ["Texto da página 3."]
    assert calls == [(1, 12), (11, 12), (12, 12)]


def test_extract_blocks_without_progress(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf([["Só uma página."]]))
    assert pdf.extract_blocks(str(path)) == [["Só uma página."]]


def test_build_document_offsets_match_page_text(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf([["Primeiro bloco aqui.", "Segundo bloco. Com duas frases."], ["* * *"], ["Fim do livro."]]))
    doc = pdf.build_document(str(path))
    pages, chunks = doc["pages"], doc["chunks"]
    assert [p["text"] for p in pages] == ["Primeiro bloco aqui.\n\nSegundo bloco. Com duas frases.", "* * *",
                                          "Fim do livro."]
    for c in chunks:
        assert pages[c["page"]]["text"][c["start"]:c["end"]] == c["text"]
    assert pages[0]["first"] == 0 and pages[0]["last"] == 1
    assert pages[1]["first"] == 2 and pages[1]["last"] == 1  # page without readable chunks
    assert pages[2]["first"] == pages[2]["last"] == 2


def test_build_document_trims_whitespace_at_chunk_edges(monkeypatch):
    para = "x" * 340 + "  " + "y" * 100  # cutting at a space leaves wide whitespace at the start
    monkeypatch.setattr(pdf, "extract_blocks", lambda path, progress=None: [[para]])
    chunks = pdf.build_document("ignorado")["chunks"]
    assert [c["text"] for c in chunks] == ["x" * 340, "y" * 100]
    assert chunks[1]["start"] == 342


@pytest.mark.parametrize("extra", [[], ["1"]])
def test_cli_prints_summary(tmp_path, monkeypatch, capsys, extra):
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf([["Primeira frase."], ["Segunda frase."]]))
    monkeypatch.setattr(sys, "argv", ["pdf", str(path), *extra])
    runpy.run_module("app.text.pdf", run_name="__main__")
    out = capsys.readouterr().out
    assert "pages=2 chunks=2" in out
    assert "chars: min=14" in out
    assert out.count("[p") == (1 if extra else 2)


def test_cli_with_empty_pdf(tmp_path, monkeypatch, capsys):
    path = tmp_path / "doc.pdf"
    path.write_bytes(make_pdf([[]]))
    monkeypatch.setattr(sys, "argv", ["pdf", str(path)])
    runpy.run_module("app.text.pdf", run_name="__main__")
    out = capsys.readouterr().out
    assert "chunks=0" in out and "chars:" not in out
