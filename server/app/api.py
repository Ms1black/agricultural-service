"""API устройства: телеметрия и события (контракты 4.1–4.2 PLAN.md).

Ответ на телеметрию — компактный JSON без пробелов: прошивка ищет в нём подстроки
вида "command":"water". Формат не менять, пока прошивка не переведена на ArduinoJson.
"""

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from . import calibration, store
from .auth import require_device

router = APIRouter(prefix="/api", dependencies=[Depends(require_device)])


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="ignore")  # новые поля прошивки не ломают приём

    device: str
    temp: float | None = None
    air_hum: float | None = None
    soil_raw: int | None = None
    soil_pct: int | None = None
    soil_ok: bool = False
    soil_dry: bool = False


class Event(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device: str
    type: str
    value: float | None = None
    note: str | None = None


def compact_json(data: dict) -> Response:
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return Response(content=body, media_type="application/json")


@router.post("/telemetry")
def telemetry(t: Telemetry, request: Request) -> Response:
    db = request.app.state.db
    settings = request.app.state.settings
    m = t.model_dump()
    cal = store.get_calibration(db, t.device)
    if cal and m["soil_ok"] and m["soil_raw"] is not None:
        # Проценты считает сервер по своей калибровке (C1) — та же формула, что в прошивке
        m["soil_pct"] = calibration.percent(m["soil_raw"], cal["dry_raw"], cal["wet_raw"])
    store.save_measurement(db, m)
    try:
        calibration.on_telemetry(db, t.device)
    except Exception:  # калибровка не должна ломать приём телеметрии
        logging.getLogger("calibration").exception("ошибка шага калибровки")

    resp: dict = {"command": "none"}
    cmd = store.take_command(db, t.device, settings.command_ttl_s)
    if cmd:
        resp = {"command": "water", "seconds": cmd["seconds"]}
    cal = store.get_calibration(db, t.device)
    if cal:
        resp["dry"] = cal["dry_raw"]
        resp["wet"] = cal["wet_raw"]
        if 1 <= (cal.get("stage") or 0) <= 3:
            resp["calib_stage"] = cal["stage"]       # F3: полоса Calibrating N/3
    return compact_json(resp)


@router.get("/forecast")
def forecast(request: Request, device: str = "plant1") -> dict:
    """Прогноз высыхания (M1): время до порога, траектория на 24 ч, версия модели и метрики."""
    from .ml.forecast import forecast as make_forecast
    return make_forecast(request.app.state.db, device)


@router.post("/voice")
async def voice(request: Request, x_device: str = Header(default="plant1")) -> Response:
    """Контракт 4.3: PCM 16 бит / 16 кГц → PCM ответа + X-Transcript, X-Answer, X-Answer-Id."""
    from starlette.concurrency import run_in_threadpool

    from . import voice as v
    from .voice.audio import MAX_PHRASE_S, RATE, pcm16_to_float
    st = request.app.state
    if st.stt is None:
        raise HTTPException(503, "голос выключен")
    body = await request.body()
    if len(body) > MAX_PHRASE_S * RATE * 2:
        raise HTTPException(413, "фраза длиннее 8 с")
    if len(body) < RATE // 5 * 2:
        raise HTTPException(400, "пустая фраза")
    try:
        r = await run_in_threadpool(v.handle, st.db, st.llm, st.stt, st.tts,
                                    st.settings.voice_path, x_device, pcm16_to_float(body))
    except v.engines.VoiceUnavailable as e:
        raise HTTPException(503, f"голос недоступен: {e}") from e
    return Response(content=r.pcm, media_type="audio/L16; rate=16000; channels=1", headers={
        "X-Transcript": quote(r.transcript), "X-Answer": quote(r.answer),
        "X-Answer-Id": str(r.id)})


@router.post("/event")
def event(e: Event, request: Request) -> Response:
    db = request.app.state.db
    note = e.note
    if e.type == "water" and note == "web":
        # Прошивка не знает, кто попросил полив: берём причину отданной ей команды
        note = store.command_reason(db, e.device) or note
    store.save_event(db, e.device, e.type, e.value, note)
    return compact_json({"ok": True})
