"""API устройства: телеметрия и события (контракты 4.1–4.2 PLAN.md).

Ответ на телеметрию — компактный JSON без пробелов: прошивка ищет в нём подстроки
вида "command":"water". Формат не менять, пока прошивка не переведена на ArduinoJson.
"""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from . import store
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
    store.save_measurement(db, t.model_dump())

    resp: dict = {"command": "none"}
    cmd = store.take_command(db, t.device, settings.command_ttl_s)
    if cmd:
        resp = {"command": "water", "seconds": cmd["seconds"]}
    cal = store.get_calibration(db, t.device)
    if cal:
        resp["dry"] = cal["dry_raw"]
        resp["wet"] = cal["wet_raw"]
    return compact_json(resp)


@router.get("/forecast")
def forecast(request: Request, device: str = "plant1") -> dict:
    """Прогноз высыхания (M1): время до порога, траектория на 24 ч, версия модели и метрики."""
    from .ml.forecast import forecast as make_forecast
    return make_forecast(request.app.state.db, device)


@router.post("/event")
def event(e: Event, request: Request) -> Response:
    store.save_event(request.app.state.db, e.device, e.type, e.value, e.note)
    return compact_json({"ok": True})
