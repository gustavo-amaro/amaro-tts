"""Testes de ponta a ponta da API HTTP, com o worker real e um sintetizador falso."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.api import documents, exports
from app.container import build_container
from app.errors import AppError
from app.server import create_app
from conftest import FakeSynth, make_pdf, wait_until, wav_bytes

PDF = make_pdf([["Primeira página aqui. Tem duas frases."], ["Esta página vai dar FALHA."], ["Última página."]])


@pytest.fixture
def container(tmp_path):
    return build_container(FakeSynth(), tmp_path)


@pytest.fixture
def client(container):
    with TestClient(create_app(container)) as c:
        wait_until(lambda: c.get("/api/status").json()["engine"] == "ready")
        yield c


@pytest.fixture
def doc_id(client):
    r = client.post("/api/docs", files={"file": ("livro.pdf", PDF, "application/pdf")})
    assert r.json()["status"] == "extracting"
    return r.json()["id"]


def endpoint(router, path):
    return next(r.endpoint for r in router.routes if r.path == path)


def test_static_and_index(client):
    assert "<html" in client.get("/").text.lower()
    assert client.get("/static/app.js").status_code == 200


def test_status(client):
    assert client.get("/api/status").json() == {
        "engine": "ready", "settings": {"steps": 6, "cfg": 2.0, "seed": 42}, "voice": False}


def test_document_flow(client, doc_id):
    # a extração roda em background task, que o TestClient conclui antes de devolver a resposta
    meta = client.get(f"/api/docs/{doc_id}").json()
    assert meta["status"] == "ready" and meta["pages"] == 3 and meta["progress"] == 0
    assert client.post("/api/docs", files={"file": ("x.pdf", PDF)}).json() == {"id": doc_id, "status": "ready"}
    assert [d["id"] for d in client.get("/api/docs").json()] == [doc_id]

    page = client.get(f"/api/docs/{doc_id}/pages/0").json()
    assert page["text"] == "Primeira página aqui. Tem duas frases." and page["chunks"][0]["id"] == 0

    assert client.post(f"/api/docs/{doc_id}/cursor", json={"chunk": 0}).json()["chunk"] == 0
    wait_until(lambda: client.get(f"/api/docs/{doc_id}/audio/0").status_code == 200)
    r = client.get(f"/api/docs/{doc_id}/audio/0")
    assert r.headers["content-type"] == "audio/ogg" and r.headers["cache-control"] == "no-store"
    wait_until(lambda: client.get(f"/api/docs/{doc_id}/audio/1").status_code == 410)
    assert client.get(f"/api/docs/{doc_id}/audio/1").json() == {"detail": "falha ao gerar este trecho"}
    assert client.get(f"/api/docs/{doc_id}/audio/99").status_code == 202
    wait_until(lambda: client.get(f"/api/docs/{doc_id}/pages/2").json()["ready"] == [2])
    assert client.get("/api/docs").json()[0]["progress"] == 0


@pytest.mark.parametrize("method, path, kwargs, status, detail", [
    ("post", "/api/docs", {"files": {"file": ("x.txt", b"oi")}}, 400, "arquivo não é um PDF"),
    ("get", "/api/docs/nada", {}, 404, "documento não encontrado"),
    ("post", "/api/exports", {"json": {"text": " "}}, 400, "texto vazio"),
    ("get", "/api/exports/nada/audio", {}, 404, "áudio não disponível"),
    ("delete", "/api/exports/nada", {}, 404, "não encontrado"),
    ("post", "/api/voice", {"files": {"file": ("v.wav", b"lixo")}}, 400,
     "formato de áudio não suportado (use wav, flac, ogg ou mp3)"),
])
def test_domain_errors_become_http_errors(client, method, path, kwargs, status, detail):
    r = getattr(client, method)(path, **kwargs)
    assert (r.status_code, r.json()) == (status, {"detail": detail})


def test_page_errors(client, doc_id, container, tmp_path):
    assert client.get(f"/api/docs/{doc_id}/pages/9").status_code == 404
    folder = tmp_path / "docs" / "emprocesso"
    folder.mkdir()
    (folder / "meta.json").write_text(json.dumps({"id": "emprocesso", "status": "extracting"}))
    r = client.get("/api/docs/emprocesso/pages/0")
    assert (r.status_code, r.json()) == (409, {"detail": "documento ainda em processamento"})


def test_unmapped_app_error_is_500(container):
    app = create_app(container)

    @app.get("/boom")
    def boom():
        raise AppError("falhou")

    with TestClient(app) as c:
        r = c.get("/boom")
    assert (r.status_code, r.json()) == (500, {"detail": "falhou"})


def test_settings_and_voice(client):
    assert client.post("/api/settings", json={"steps": 8, "cfg": None}).json()["settings"]["steps"] == 8
    assert client.post("/api/voice", files={"file": ("v.wav", wav_bytes(1))}).json()["voice"] is True
    assert client.delete("/api/voice").json()["voice"] is False


def test_export_flow(client):
    job = client.post("/api/exports", json={"text": "Olá mundo.\n\nSegundo parágrafo.", "style": "(alegre)"}).json()
    assert job["status"] == "queued" and job["style"] == "alegre"
    wait_until(lambda: client.get("/api/exports").json()[0]["status"] == "done")
    r = client.get(f"/api/exports/{job['id']}/audio")
    assert r.headers["content-type"] == "audio/mpeg" and "content-disposition" not in r.headers
    r = client.get(f"/api/exports/{job['id']}/audio", params={"download": True})
    assert r.headers["content-disposition"].startswith("attachment")
    assert "Ol%C3%A1%20mundo%20Segundo%20par%C3%A1grafo.mp3" in r.headers["content-disposition"]
    assert client.delete(f"/api/exports/{job['id']}").json() == {"ok": True}
    assert client.get("/api/exports").json() == []


@pytest.mark.parametrize("module, path, args", [
    (documents, "/api/docs/{doc_id}/events", ("d1",)), (exports, "/api/exports/events", ()),
])
def test_event_streams_start_with_engine_status(container, module, path, args):
    router = module.create_router(container)

    async def first_frame():
        response = await endpoint(router, path)(*args)
        it = response.body_iterator
        frame = await it.__anext__()
        await it.aclose()
        return response.media_type, frame

    media_type, frame = asyncio.run(first_frame())
    assert media_type == "text/event-stream"
    assert json.loads(frame[6:]) == {"type": "engine", "status": "loading"}


def test_module_level_app_is_built():
    from app import server

    assert "/api/status" in server.app.openapi()["paths"]
