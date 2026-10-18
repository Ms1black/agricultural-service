"""Точка входа: uvicorn app.main:create_app --factory"""

from fastapi import FastAPI

from . import api, web
from .config import Settings
from .db import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.check()
    db = Database(settings.db_path)
    db.migrate()

    app = FastAPI(title="Plant care", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.db = db

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "schema": db.version()}

    app.include_router(api.router)
    app.include_router(web.router)
    return app
