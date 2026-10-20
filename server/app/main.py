"""Точка входа: uvicorn app.main:create_app --factory"""

from fastapi import FastAPI

from . import api, web
from .config import Settings
from .db import Database
from .llm import make_provider


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.check()
    db = Database(settings.db_path)
    db.migrate()

    app = FastAPI(title="Plant care", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.db = db
    app.state.llm = make_provider(settings)

    @app.get("/health")
    def health() -> dict:
        llm = app.state.llm.name if app.state.llm else "none"
        return {"status": "ok", "schema": db.version(), "llm": llm}

    app.include_router(api.router)
    app.include_router(web.router)
    return app
