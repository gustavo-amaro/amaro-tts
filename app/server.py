"""Servidor web do leitor de PDF.  Rodar: .venv/bin/uvicorn app.server:app --port 8000"""
import asyncio
import hashlib
import io
import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import soundfile as sf
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .engine import Engine
from .pdf_text import build_document, chunk_paragraph

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / "data"
DOCS = DATA / "docs"
DOCS.mkdir(parents=True, exist_ok=True)
EXPORT_MAX_CHARS = 50_000

engine = Engine(DATA)
_loaded: dict[str, dict] = {}  # doc_id -> {"pages", "chunks", "texts"}
_subscribers: dict[str, set[asyncio.Queue]] = {}
_loop: asyncio.AbstractEventLoop | None = None


# ---------- eventos (SSE) ----------
def publish(doc_id: str, event: dict):
    """Pode ser chamado de qualquer thread."""
    if _loop is None:
        return
    for q in list(_subscribers.get(doc_id, ())):
        _loop.call_soon_threadsafe(q.put_nowait, event)


@asynccontextmanager
async def lifespan(_app):
    global _loop
    _loop = asyncio.get_running_loop()
    engine.on_event = publish
    engine.start()
    yield


app = FastAPI(lifespan=lifespan)


# ---------- documentos ----------
def _meta_path(doc_id: str) -> Path:
    return DOCS / doc_id / "meta.json"


def _read_meta(doc_id: str) -> dict:
    p = _meta_path(doc_id)
    if not p.exists():
        raise HTTPException(404, "documento não encontrado")
    return json.loads(p.read_text())


def _write_meta(meta: dict):
    _meta_path(meta["id"]).write_text(json.dumps(meta, ensure_ascii=False))


def _doc(doc_id: str) -> dict:
    if doc_id not in _loaded:
        p = DOCS / doc_id / "chunks.json"
        if not p.exists():
            raise HTTPException(409, "documento ainda em processamento")
        data = json.loads(p.read_text())
        data["texts"] = [c["text"] for c in data["chunks"]]
        _loaded[doc_id] = data
    return _loaded[doc_id]


def _progress(doc_id: str) -> int:
    p = DOCS / doc_id / "progress.json"
    return json.loads(p.read_text())["chunk"] if p.exists() else 0


def _extract(doc_id: str):
    meta = _read_meta(doc_id)
    t0 = time.time()

    def progress(i, n):
        publish(doc_id, {"type": "extract_progress", "done": i, "total": n})

    try:
        data = build_document(str(DOCS / doc_id / "doc.pdf"), progress)
    except Exception as e:
        meta.update(status="error", error=str(e))
        _write_meta(meta)
        publish(doc_id, {"type": "extract_error", "error": str(e)})
        return
    (DOCS / doc_id / "chunks.json").write_text(json.dumps(data, ensure_ascii=False))
    meta.update(
        status="ready",
        pages=len(data["pages"]),
        chunks=len(data["chunks"]),
        page_first=[p["first"] for p in data["pages"]],
        extract_seconds=round(time.time() - t0, 2),
    )
    _write_meta(meta)
    publish(doc_id, {"type": "extract_done", "pages": meta["pages"], "chunks": meta["chunks"]})


@app.get("/api/status")
def status():
    return {"engine": engine.status, "settings": engine.settings, "voice": engine.voice_path.exists()}


@app.get("/api/docs")
def list_docs():
    metas = [json.loads(p.read_text()) for p in DOCS.glob("*/meta.json")]
    for m in metas:
        m.pop("page_first", None)
        m["progress"] = _progress(m["id"])
    return sorted(metas, key=lambda m: m.get("opened", 0), reverse=True)


@app.post("/api/docs")
async def upload_doc(file: UploadFile = File(...)):
    content = await file.read()
    if not content.startswith(b"%PDF"):
        raise HTTPException(400, "arquivo não é um PDF")
    doc_id = hashlib.sha1(content).hexdigest()[:16]
    folder = DOCS / doc_id
    if _meta_path(doc_id).exists() and _read_meta(doc_id)["status"] == "ready":
        return {"id": doc_id, "status": "ready"}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "doc.pdf").write_bytes(content)
    _write_meta({"id": doc_id, "name": file.filename or "documento.pdf", "status": "extracting", "opened": time.time()})
    asyncio.get_running_loop().run_in_executor(None, _extract, doc_id)
    return {"id": doc_id, "status": "extracting"}


@app.get("/api/docs/{doc_id}")
def get_doc(doc_id: str):
    meta = _read_meta(doc_id)
    meta["progress"] = _progress(doc_id)
    if meta["status"] == "ready":
        meta["opened"] = time.time()
        _write_meta({k: v for k, v in meta.items() if k != "progress"})
    return meta


@app.get("/api/docs/{doc_id}/pages/{n}")
def get_page(doc_id: str, n: int):
    data = _doc(doc_id)
    if not 0 <= n < len(data["pages"]):
        raise HTTPException(404, "página inexistente")
    page = data["pages"][n]
    ids = range(page["first"], page["last"] + 1)
    return {
        "page": n,
        "text": page["text"],
        "chunks": [{"id": i, "start": data["chunks"][i]["start"], "end": data["chunks"][i]["end"]} for i in ids],
        "ready": [i for i in ids if engine.is_ready(doc_id, i)],
    }


class Cursor(BaseModel):
    chunk: int


@app.post("/api/docs/{doc_id}/cursor")
def set_cursor(doc_id: str, body: Cursor):
    data = _doc(doc_id)
    chunk = max(0, min(body.chunk, len(data["texts"]) - 1))
    (DOCS / doc_id / "progress.json").write_text(json.dumps({"chunk": chunk}))
    engine.set_cursor(doc_id, data["texts"], chunk)
    return {"chunk": chunk, "ready": engine.ready_ahead(doc_id, chunk)}


@app.get("/api/docs/{doc_id}/audio/{chunk}")
def get_audio(doc_id: str, chunk: int):
    path = engine.audio_path(doc_id, chunk)
    if path.exists():
        return FileResponse(path, media_type="audio/ogg", headers={"Cache-Control": "no-store"})
    if engine.is_failed(doc_id, chunk):
        raise HTTPException(410, "falha ao gerar este trecho")
    return Response(status_code=202)


def _sse(channel: str) -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(channel, set()).add(q)

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'engine', 'status': engine.status})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'engine', 'status': engine.status})}\n\n"
        finally:
            _subscribers[channel].discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/docs/{doc_id}/events")
async def events(doc_id: str):
    return _sse(doc_id)


# ---------- texto -> áudio para download ----------
class ExportRequest(BaseModel):
    text: str
    style: str | None = None


@app.post("/api/exports")
def create_export(body: ExportRequest):
    text = body.text.replace("\r\n", "\n").strip()
    if not text:
        raise HTTPException(400, "texto vazio")
    if len(text) > EXPORT_MAX_CHARS:
        raise HTTPException(400, f"texto muito longo (máx. {EXPORT_MAX_CHARS} caracteres)")
    texts, paras = [], []
    for pno, para in enumerate(p for p in re.split(r"\n\s*\n", text) if p.strip()):
        para = re.sub(r"\s+", " ", para).strip()
        for s, e in chunk_paragraph(para):
            piece = para[s:e].strip()
            if re.search(r"\w", piece):
                texts.append(piece)
                paras.append(pno)
    if not texts:
        raise HTTPException(400, "nenhum texto legível")
    title = " ".join(text.split()[:8])[:60]
    style = (body.style or "").strip().strip("()") or None
    return engine.create_export(uuid.uuid4().hex[:12], title, texts, paras, style, dict(engine.settings))


@app.get("/api/exports")
def list_exports():
    return engine.list_exports()


@app.get("/api/exports/events")
async def export_events():
    return _sse("exports")


@app.get("/api/exports/{job_id}/audio")
def export_audio(job_id: str, download: bool = False):
    job = engine.get_export(job_id)
    path = engine.exports_dir / job_id / "audio.mp3"
    if not job or job["status"] != "done" or not path.exists():
        raise HTTPException(404, "áudio não disponível")
    if not download:
        return FileResponse(path, media_type="audio/mpeg")
    name = re.sub(r"[^\w\- ]+", "", job["title"]).strip()[:50] or "audio"
    return FileResponse(path, media_type="audio/mpeg", filename=f"{name}.mp3")


@app.delete("/api/exports/{job_id}")
def delete_export(job_id: str):
    if not engine.get_export(job_id):
        raise HTTPException(404, "não encontrado")
    engine.delete_export(job_id)
    return {"ok": True}


# ---------- configurações e voz ----------
class Settings(BaseModel):
    steps: int | None = None
    cfg: float | None = None
    seed: int | None = None


@app.post("/api/settings")
def update_settings(body: Settings):
    engine.update_settings(**body.model_dump())
    return status()


@app.post("/api/voice")
async def upload_voice(file: UploadFile = File(...)):
    try:
        wav, sr = sf.read(io.BytesIO(await file.read()), always_2d=True)
    except Exception:
        raise HTTPException(400, "formato de áudio não suportado (use wav, flac, ogg ou mp3)")
    sf.write(engine.voice_path, wav.mean(axis=1)[: sr * 30], sr)  # mono, até 30 s
    engine.voice_changed()
    return status()


@app.delete("/api/voice")
def delete_voice():
    engine.voice_path.unlink(missing_ok=True)
    engine.voice_changed()
    return status()


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

