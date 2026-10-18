"""Веб-интерфейс: дашборд, ручной полив, калибровка, выгрузка CSV. Всё под HTTP Basic."""

import csv
import io
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from .. import store
from ..auth import require_web

router = APIRouter(dependencies=[Depends(require_web)])

INDEX_HTML = Path(__file__).with_name("index.html")
EXPORT_TABLES = {
    "measurements": "SELECT id, ts, device, temp, air_hum, soil_raw, soil_pct, soil_ok, "
                    "soil_dry_d0 FROM measurements ORDER BY id",
    "events": "SELECT id, ts, device, type, value, note FROM events ORDER BY id",
}


class WaterRequest(BaseModel):
    device: str = "plant1"
    seconds: int = 3


class CalibrationRequest(BaseModel):
    device: str = "plant1"
    dry_raw: int | None = None
    wet_raw: int | None = None
    # "dry" / "wet" — взять текущее сырое значение датчика
    use_current: str | None = None


@router.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@router.get("/web/api/devices")
def list_devices(request: Request) -> list[str]:
    return store.devices(request.app.state.db)


@router.get("/web/api/state")
def state(request: Request, device: str = "plant1") -> dict:
    db = request.app.state.db
    settings = request.app.state.settings
    return {
        "now": time.time(),
        "latest": store.latest_measurement(db, device),
        "calibration": store.get_calibration(db, device),
        "pending": store.pending_command(db, device, settings.command_ttl_s),
        "events": store.recent_events(db, device),
    }


@router.get("/web/api/history")
def history(request: Request, device: str = "plant1", hours: float = 24) -> list[dict]:
    hours = max(0.1, min(hours, 24 * 31))
    return store.measurements_since(request.app.state.db, device, time.time() - hours * 3600)


@router.post("/web/api/water")
def water(req: WaterRequest, request: Request) -> dict:
    seconds = store.queue_water(request.app.state.db, req.device, req.seconds)
    return {"queued": True, "seconds": seconds}


@router.post("/web/api/calibration")
def calibration(req: CalibrationRequest, request: Request) -> dict:
    db = request.app.state.db
    cal = store.get_calibration(db, req.device) or {"dry_raw": 4000, "wet_raw": 1150}
    dry, wet = req.dry_raw or cal["dry_raw"], req.wet_raw or cal["wet_raw"]
    if req.use_current:
        latest = store.latest_measurement(db, req.device)
        if not latest or latest["soil_raw"] is None:
            raise HTTPException(400, "нет данных с датчика")
        if req.use_current == "dry":
            dry = latest["soil_raw"]
        elif req.use_current == "wet":
            wet = latest["soil_raw"]
        else:
            raise HTTPException(400, "use_current: dry или wet")
    if not (0 <= wet < dry <= 4095):
        raise HTTPException(400, "нужно 0 ≤ мокро < сухо ≤ 4095")
    store.set_calibration(db, req.device, dry, wet)
    return {"dry_raw": dry, "wet_raw": wet}


@router.get("/web/api/export.csv")
def export_csv(request: Request, table: str = "measurements") -> StreamingResponse:
    if table not in EXPORT_TABLES:
        raise HTTPException(400, "table: measurements или events")
    with request.app.state.db.conn() as c:
        cur = c.execute(EXPORT_TABLES[table])
        header = [d[0] for d in cur.description]
        rows = cur.fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(tuple(r) for r in rows)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
    )
