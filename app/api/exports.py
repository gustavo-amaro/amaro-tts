from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..container import Container
from ..tts.exports import CHANNEL
from .sse import sse_response


class ExportRequest(BaseModel):
    text: str
    style: str | None = None


def create_router(c: Container) -> APIRouter:
    router = APIRouter(prefix="/api/exports")

    @router.post("")
    def create_export(body: ExportRequest):
        return c.exports.create(body.text, body.style)

    @router.get("")
    def list_exports():
        return c.exports.list()

    @router.get("/events")
    async def export_events():
        return sse_response(c.bus, CHANNEL, lambda: {"type": "engine", "status": c.worker.status})

    @router.get("/{job_id}/audio")
    def export_audio(job_id: str, download: bool = False):
        path, filename = c.exports.audio(job_id)
        return FileResponse(path, media_type="audio/mpeg", filename=filename if download else None)

    @router.delete("/{job_id}")
    def delete_export(job_id: str):
        c.exports.delete(job_id)
        return {"ok": True}

    return router
