"""Точка входа: uvicorn app.main:create_app --factory"""

import logging
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from . import api, web
from .auth import require_web
from .config import Settings
from .db import Database
from .llm import make_provider


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.check()
    db = Database(settings.db_path)
    db.migrate()

    # Стандартные /docs и /openapi.json выключены: Swagger — под паролем веба (docs_router)
    app = FastAPI(title="Plant care", description=DESCRIPTION,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.db = db
    app.state.llm = make_provider(settings)
    from . import safety
    safety.demo_mode = settings.demo_mode
    app.state.stt = app.state.tts = None
    if settings.voice_enabled:
        from .voice.engines import Silero, Whisper
        root = Path(settings.models_path) / "voice"
        app.state.stt = Whisper(settings.whisper_model, root)
        app.state.tts = Silero(settings.tts_speaker, root)
        if settings.voice_preload:
            threading.Thread(target=warm_up, args=(app,), daemon=True).start()

    @app.get("/health", tags=["Сервис"])
    def health() -> dict:
        llm = app.state.llm.name if app.state.llm else "none"
        return {"status": "ok", "schema": db.version(), "llm": llm,
                "voice": app.state.stt is not None, "demo": settings.demo_mode}

    if settings.retrain_hours > 0:
        from .ml.monitor import start_scheduler
        start_scheduler(db, settings.models_path, settings.retrain_hours)

    app.include_router(api.router)
    app.include_router(web.router)
    app.include_router(docs_router(app))
    return app


DESCRIPTION = """API ПАК ухода за растениями (контракты — раздел 4 PLAN.md).

* `/api/*` — устройство, заголовок `X-Api-Key` (PLANT_TOKEN).
* `/web/api/*` — веб-интерфейс, HTTP Basic (PLANT_WEB_USER / PLANT_WEB_PASSWORD).

Для «Try it out» нажмите **Authorize** и задайте ключ устройства и/или пароль веба.
"""


def docs_router(app: FastAPI) -> APIRouter:
    """Swagger UI и схема OpenAPI — под /web/api (nginx уже проксирует), под паролем веба."""
    router = APIRouter(prefix="/web/api", dependencies=[Depends(require_web)],
                       include_in_schema=False)

    @router.get("/openapi.json")
    def openapi() -> dict:
        return app.openapi()

    @router.get("/docs")
    def docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/web/api/openapi.json",
                                   title=f"{app.title} — API")

    return router


def warm_up(app: FastAPI) -> None:
    """Загрузить модели голоса заранее, чтобы первая фраза не ждала скачивания."""
    import numpy as np
    try:
        app.state.stt.transcribe(np.zeros(16000, dtype=np.float32))
        app.state.tts.synthesize("Готов.")
        logging.getLogger("voice").info("модели голоса загружены")
    except Exception as e:
        logging.getLogger("voice").warning("голос недоступен: %s", e)
