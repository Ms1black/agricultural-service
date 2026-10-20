"""Точка входа: uvicorn app.main:create_app --factory"""

import logging
import threading
from pathlib import Path

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

    @app.get("/health")
    def health() -> dict:
        llm = app.state.llm.name if app.state.llm else "none"
        return {"status": "ok", "schema": db.version(), "llm": llm,
                "voice": app.state.stt is not None, "demo": settings.demo_mode}

    if settings.retrain_hours > 0:
        from .ml.monitor import start_scheduler
        start_scheduler(db, settings.models_path, settings.retrain_hours)

    app.include_router(api.router)
    app.include_router(web.router)
    return app


def warm_up(app: FastAPI) -> None:
    """Загрузить модели голоса заранее, чтобы первая фраза не ждала скачивания."""
    import numpy as np
    try:
        app.state.stt.transcribe(np.zeros(16000, dtype=np.float32))
        app.state.tts.synthesize("Готов.")
        logging.getLogger("voice").info("модели голоса загружены")
    except Exception as e:
        logging.getLogger("voice").warning("голос недоступен: %s", e)
