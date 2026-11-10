import json

import pytest

from app.documents.repository import DocumentRepository
from app.documents.service import DocumentService
from app.errors import Conflict, InvalidInput, NotFound

PDF = b"%PDF-1.4 conteudo"
DATA = {
    "pages": [{"text": "Um. Dois.", "first": 0, "last": 1}, {"text": "Três.", "first": 2, "last": 2}],
    "chunks": [{"page": 0, "start": 0, "end": 3, "text": "Um."}, {"page": 0, "start": 4, "end": 9, "text": "Dois."},
               {"page": 1, "start": 0, "end": 5, "text": "Três."}],
}


class FakeReader:
    def __init__(self):
        self.ready: set[int] = set()
        self.cursor = None

    def is_ready(self, doc_id, idx):
        return idx in self.ready

    def ready_ahead(self, doc_id, cursor):
        return sorted(i for i in self.ready if i >= cursor)

    def set_cursor(self, doc_id, texts, cursor):
        self.cursor = (doc_id, texts, cursor)


@pytest.fixture
def repo(tmp_path):
    return DocumentRepository(tmp_path / "docs")


@pytest.fixture
def reader():
    return FakeReader()


@pytest.fixture
def extracted():
    return []


@pytest.fixture
def service(repo, reader, publish, extracted):
    def extractor(path, progress):
        extracted.append(path)
        progress(1, 2)
        if b"QUEBRADO" in open(path, "rb").read():
            raise ValueError("pdf corrompido")
        return json.loads(json.dumps(DATA))

    return DocumentService(repo, reader, extractor, publish)


def ready_doc(service, content=PDF):
    doc_id = service.upload("livro.pdf", content)["id"]
    service.extract(doc_id)
    return doc_id


# ---------- repository ----------
def test_repository_missing_files(repo):
    assert not repo.exists("x")
    with pytest.raises(NotFound):
        repo.read_meta("x")
    with pytest.raises(Conflict):
        repo.read_chunks("x")
    assert repo.read_progress("x") == 0
    assert repo.all_metas() == []


def test_repository_roundtrip(repo):
    repo.save_pdf("d", PDF)
    assert repo.pdf_path("d").read_bytes() == PDF
    repo.write_meta({"id": "d", "name": "ç"})
    assert repo.exists("d") and repo.read_meta("d") == {"id": "d", "name": "ç"}
    repo.write_chunks("d", DATA)
    assert repo.read_chunks("d") == DATA
    repo.write_progress("d", 5)
    assert repo.read_progress("d") == 5
    assert repo.all_metas() == [{"id": "d", "name": "ç"}]


# ---------- upload and extraction ----------
def test_upload_rejects_non_pdf(service):
    with pytest.raises(InvalidInput):
        service.upload("x.txt", b"texto")


def test_upload_new_pdf(service, repo):
    result = service.upload(None, PDF)
    assert result["status"] == "extracting" and len(result["id"]) == 16
    meta = repo.read_meta(result["id"])
    assert meta["name"] == "document.pdf" and meta["status"] == "extracting"
    assert repo.pdf_path(result["id"]).read_bytes() == PDF


def test_upload_same_pdf_is_deduplicated_once_ready(service):
    first = service.upload("a.pdf", PDF)
    assert service.upload("a.pdf", PDF) == first  # still extracting: extract again
    service.extract(first["id"])
    assert service.upload("b.pdf", PDF) == {"id": first["id"], "status": "ready"}


def test_extract_success(service, repo, publish, extracted):
    doc_id = service.upload("a.pdf", PDF)["id"]
    service.extract(doc_id)
    assert extracted == [str(repo.pdf_path(doc_id))]
    meta = repo.read_meta(doc_id)
    assert meta["status"] == "ready" and meta["pages"] == 2 and meta["chunks"] == 3
    assert meta["page_first"] == [0, 2] and meta["extract_seconds"] >= 0
    assert publish.of(doc_id) == [{"type": "extract_progress", "done": 1, "total": 2},
                                  {"type": "extract_done", "pages": 2, "chunks": 3}]


def test_extract_failure(service, repo, publish):
    doc_id = service.upload("a.pdf", PDF + b"QUEBRADO")["id"]
    service.extract(doc_id)
    meta = repo.read_meta(doc_id)
    assert meta["status"] == "error" and meta["error"] == "pdf corrompido"
    assert publish.of(doc_id, "extract_error") == [{"type": "extract_error", "error": "pdf corrompido"}]


# ---------- reading ----------
def test_list_sorted_by_last_opened(service, repo, monkeypatch):
    a = ready_doc(service, PDF + b"a")
    b = ready_doc(service, PDF + b"b")
    repo.write_progress(a, 2)
    service.open(a)
    docs = service.list()
    assert [d["id"] for d in docs] == [a, b]
    assert docs[0]["progress"] == 2 and "page_first" not in docs[0]


def test_open_updates_last_opened_only_when_ready(service, repo):
    extracting = service.upload("a.pdf", PDF + b"x")["id"]
    before = repo.read_meta(extracting)["opened"]
    assert service.open(extracting)["opened"] == before

    doc_id = ready_doc(service)
    repo.write_progress(doc_id, 1)
    old = repo.read_meta(doc_id)["opened"]
    meta = service.open(doc_id)
    assert meta["progress"] == 1 and meta["opened"] >= old
    assert "progress" not in repo.read_meta(doc_id)
    with pytest.raises(NotFound):
        service.open("nada")


def test_page(service, reader, repo):
    doc_id = ready_doc(service)
    reader.ready = {1}
    assert service.page(doc_id, 0) == {
        "page": 0, "text": "Um. Dois.",
        "chunks": [{"id": 0, "start": 0, "end": 3}, {"id": 1, "start": 4, "end": 9}],
        "ready": [1],
    }
    for n in (-1, 2):
        with pytest.raises(NotFound):
            service.page(doc_id, n)


def test_content_is_cached_in_memory(service, repo, tmp_path):
    doc_id = ready_doc(service)
    service.page(doc_id, 0)
    (tmp_path / "docs" / doc_id / "chunks.json").unlink()
    assert service.page(doc_id, 1)["text"] == "Três."


def test_page_before_extraction_conflicts(service):
    doc_id = service.upload("a.pdf", PDF)["id"]
    with pytest.raises(Conflict):
        service.page(doc_id, 0)


@pytest.mark.parametrize("requested, expected", [(-5, 0), (1, 1), (99, 2)])
def test_set_cursor_clamps_persists_and_schedules(service, reader, repo, requested, expected):
    doc_id = ready_doc(service)
    reader.ready = {0, 2}
    result = service.set_cursor(doc_id, requested)
    assert result == {"chunk": expected, "ready": [i for i in (0, 2) if i >= expected]}
    assert repo.read_progress(doc_id) == expected
    assert reader.cursor == (doc_id, ["Um.", "Dois.", "Três."], expected)
