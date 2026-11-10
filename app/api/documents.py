from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ..container import Container
from .sse import sse_response


class Cursor(BaseModel):
    chunk: int


def create_router(c: Container) -> APIRouter:
    router = APIRouter(prefix="/api/docs")

    @router.get("")
    def list_docs():
        return c.documents.list()

    @router.post("")
    async def upload_doc(background: BackgroundTasks, file: UploadFile = File(...)):
        result = c.documents.upload(file.filename, await file.read())
        if result["status"] == "extracting":
            background.add_task(c.documents.extract, result["id"])
        return result

    @router.get("/{doc_id}")
    def get_doc(doc_id: str):
        return c.documents.open(doc_id)

    @router.get("/{doc_id}/pages/{n}")
    def get_page(doc_id: str, n: int):
        return c.documents.page(doc_id, n)

    @router.post("/{doc_id}/cursor")
    def set_cursor(doc_id: str, body: Cursor):
        return c.documents.set_cursor(doc_id, body.chunk)

    @router.get("/{doc_id}/audio/{chunk}")
    def get_audio(doc_id: str, chunk: int):
        path = c.reader.audio_path(doc_id, chunk)
        if path.exists():
            return FileResponse(path, media_type="audio/ogg", headers={"Cache-Control": "no-store"})
        if c.reader.is_failed(doc_id, chunk):
            raise HTTPException(410, "failed to generate this chunk")
        return Response(status_code=202)  # not generated yet

    @router.get("/{doc_id}/events")
    async def events(doc_id: str):
        return sse_response(c.bus, doc_id, lambda: {"type": "engine", "status": c.worker.status})

    return router
