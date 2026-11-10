"""Engine status, synthesis settings and reference voice."""
from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from ..container import Container


class Settings(BaseModel):
    steps: int | None = None
    cfg: float | None = None
    seed: int | None = None


def create_router(c: Container) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/status")
    def status():
        return {"engine": c.worker.status, "settings": c.synthesis.settings, "voice": c.synthesis.has_voice()}

    @router.post("/settings")
    def update_settings(body: Settings):
        c.synthesis.update(**body.model_dump())
        return status()

    @router.post("/voice")
    async def upload_voice(file: UploadFile = File(...)):
        c.synthesis.set_voice(await file.read())
        return status()

    @router.delete("/voice")
    def delete_voice():
        c.synthesis.delete_voice()
        return status()

    return router
