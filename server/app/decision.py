"""Модуль решений (D0): режимы threshold / ml, каждое решение — в decisions с причиной.

threshold — поливает устройство само по порогу 30 % (как эталонная прошивка), сервер не вмешивается.
ml — сервер поливает по прогнозу: когда до нижней границы диапазона остаётся меньше ML_LEAD_H,
длительность — доза из калибровки (C1). Любой полив — через слой безопасности (раздел 5).
Режим ml доступен, только если уверенность калибровки ≥ medium; иначе — локальный порог.
"""

import time

from . import calibration, safety, store
from .db import Database

MODES = ("threshold", "ml")
ML_LEAD_H = 0.5                 # полить, если до порога меньше получаса
DEFAULT_SECONDS = 3
REPEAT_LOG_S = 30 * 60          # одинаковые «не поливаю» — не чаще раза в 30 мин


def get_mode(db: Database, device: str) -> str:
    with db.conn() as c:
        row = c.execute("SELECT mode FROM device_settings WHERE device = ?", (device,)).fetchone()
    return row["mode"] if row else "threshold"


def set_mode(db: Database, device: str, mode: str) -> dict:
    if mode not in MODES:
        raise ValueError("режим: threshold или ml")
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO device_settings (device, mode, updated) VALUES (?, ?, ?)",
                  (device, mode, time.time()))
    ok, why = ml_available(db, device)
    log(db, device, mode, "mode", 0, f"режим {mode}" + ("" if mode == "threshold" or ok
                                                        else f" (пока недоступен: {why})"))
    return status(db, device)


def ml_available(db: Database, device: str) -> tuple[bool, str]:
    cal = store.get_calibration(db, device) or {}
    conf = cal.get("confidence") or "none"
    if conf not in ("medium", "high"):
        return False, (f"уверенность калибровки «{conf}», нужна medium — это ≥ 3 цикла "
                       "полив → высыхание")
    return True, "калибровка достаточная"


def active_mode(db: Database, device: str) -> str:
    """Режим, который действует сейчас: ml без достаточной калибровки → threshold."""
    mode = get_mode(db, device)
    return "ml" if mode == "ml" and ml_available(db, device)[0] else "threshold"


def status(db: Database, device: str) -> dict:
    ok, why = ml_available(db, device)
    return {"mode": get_mode(db, device), "active": active_mode(db, device), "ml_available": ok,
            "ml_reason": why}


def log(db: Database, device: str, mode: str, action: str, seconds: float, reason: str,
        now: float | None = None) -> None:
    with db.conn() as c:
        c.execute("INSERT INTO decisions (ts, device, mode, action, seconds, reason) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (now or time.time(), device, mode, action,
                                                seconds, reason))


def recent(db: Database, device: str, limit: int = 20) -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT ts, mode, action, seconds, reason FROM decisions WHERE device = ? "
            "ORDER BY ts DESC, id DESC LIMIT ?", (device, limit))]


def _repeated(db: Database, device: str, blocked_by: str, now: float) -> bool:
    """Тот же запрет уже записан недавно — не засорять журнал каждые 30 с."""
    with db.conn() as c:
        row = c.execute("SELECT ts, action, reason FROM decisions WHERE device = ? "
                        "ORDER BY id DESC LIMIT 1", (device,)).fetchone()
    return bool(row and row["action"] == "blocked" and row["reason"].endswith(blocked_by)
                and now - row["ts"] < REPEAT_LOG_S)


def step(db: Database, device: str, now: float | None = None) -> dict | None:
    """Решение по свежей телеметрии (только режим ml)."""
    now = now or time.time()
    if active_mode(db, device) != "ml":
        return None
    from .ml.forecast import forecast
    cal = store.get_calibration(db, device) or {}
    threshold = cal.get("target_low") or 30.0
    fc = forecast(db, device, threshold=threshold, now=now)
    if fc.get("status") != "ok":
        return None
    hours = fc["hours_to_threshold"]
    if hours > ML_LEAD_H:
        return None
    seconds = calibration.dose_seconds(db, device, fc["soil"]) or DEFAULT_SECONDS
    why = (f"прогноз: до нижней границы {threshold:g} % ≈ {hours:.1f} ч "
           f"(сейчас {fc['soil']:g} %); доза {seconds} с"
           + (f" по k_dose {cal['k_dose']:g} %/с" if cal.get("k_dose") else ""))
    v = safety.request_water(db, device, seconds, "ml", now)
    if v.ok:
        log(db, device, "ml", "water", v.seconds, why, now)
        return {"action": "water", "seconds": v.seconds, "reason": why}
    reason = f"{why}; не поливаю: {v.reason}"
    if not _repeated(db, device, v.reason, now):
        log(db, device, "ml", "blocked", 0, reason, now)
    return {"action": "blocked", "reason": reason}
