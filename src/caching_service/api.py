import logging
import sqlite3
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as ApiPath
from fastapi.responses import JSONResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import OperationalError

from caching_service.schemas import PayloadCreated, PayloadInput, PayloadOutput
from caching_service.storage import Store, transform

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CACHE_")
    data_dir: Path = Path("data")


def create_app(
    settings: Settings | None = None, transformer: Callable[[str], str] = transform
) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(config.data_dir, transformer)
        app.state.store = store
        try:
            yield
        finally:
            store.close()

    app = FastAPI(title="Caching Service", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(OperationalError)
    async def database_error(_request: Request, error: OperationalError):
        if getattr(error.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY:
            return JSONResponse(
                status_code=503,
                content={"detail": "Cache is busy; retry the request"},
                headers={"Retry-After": "1"},
            )
        logger.error("Database operation failed", exc_info=error)
        return JSONResponse(status_code=500, content={"detail": "Database operation failed"})

    @app.post("/payload", response_model=PayloadCreated)
    def create_payload(body: PayloadInput, request: Request) -> PayloadCreated:
        return PayloadCreated(id=request.app.state.store.create(body))

    @app.get("/payload/{id}", response_model=PayloadOutput)
    def read_payload(
        id: Annotated[str, ApiPath(pattern=r"^[0-9a-f]{64}$")], request: Request
    ) -> PayloadOutput:
        result = request.app.state.store.read(id)
        if result is None:
            raise HTTPException(status_code=404, detail="Payload not found")
        return result

    return app


app = create_app()
