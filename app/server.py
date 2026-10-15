"""Servidor web do leitor de PDF.  Rodar: .venv/bin/uvicorn app.server:app --port 8000"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import documents, exports, system
from .config import STATIC_DIR
from .container import Container, build_container
from .errors import AppError, Conflict, InvalidInput, NotFound

ERROR_STATUS = {NotFound: 404, Conflict: 409, InvalidInput: 400}


def create_app(c: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        c.bus.bind(asyncio.get_running_loop())
        c.worker.start()
        yield

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError):
        return JSONResponse({"detail": str(exc)}, status_code=ERROR_STATUS.get(type(exc), 500))

    for module in (system, documents, exports):
        app.include_router(module.create_router(c))

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app(build_container())
