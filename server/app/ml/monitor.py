"""MLOps (M2): фактическая ошибка прогноза и ежедневное переобучение внутри сервиса.

Раз в час для устройства записываем прогноз «до порога N ч». Когда почва дошла до порога —
считаем фактическое время и ошибку; полив раньше порога — прогноз не проверяем (цензура).
"""

import logging
import threading
import time

from .. import store
from ..db import Database

LOG_EVERY_S = 3600
WINDOW_DAYS = 7
log = logging.getLogger("ml")


def log_forecast(db: Database, device: str, now: float | None = None) -> bool:
    now = now or time.time()
    with db.conn() as c:
        last = c.execute("SELECT MAX(ts) AS ts FROM forecast_log WHERE device = ?",
                         (device,)).fetchone()["ts"]
    if last and now - last < LOG_EVERY_S:
        return False
    from .forecast import forecast
    cal = store.get_calibration(db, device) or {}
    fc = forecast(db, device, threshold=cal.get("target_low") or 30.0, now=now)
    if fc.get("status") != "ok" or fc["soil"] <= fc["threshold"] or not fc.get("next_water_ts"):
        return False
    with db.conn() as c:
        c.execute("INSERT INTO forecast_log (ts, device, soil, threshold, predicted_h, model, "
                  "method) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (now, device, fc["soil"], fc["threshold"], fc["hours_to_threshold"],
                   (fc["model"] or {}).get("version"), fc["method"]))
    return True


def resolve(db: Database, device: str) -> int:
    """Закрыть прогнозы, для которых уже известен ответ."""
    n = 0
    with db.conn() as c:
        open_rows = c.execute("SELECT id, ts, threshold FROM forecast_log WHERE device = ? "
                              "AND status IS NULL", (device,)).fetchall()
        for r in open_rows:
            hit = c.execute("SELECT MIN(ts) AS ts FROM measurements WHERE device = ? AND ts > ? "
                            "AND soil_ok = 1 AND soil_pct <= ?",
                            (device, r["ts"], r["threshold"])).fetchone()["ts"]
            water = c.execute("SELECT MIN(ts) AS ts FROM events WHERE device = ? AND "
                              "type = 'water' AND ts > ?", (device, r["ts"])).fetchone()["ts"]
            if water and (hit is None or water < hit):
                c.execute("UPDATE forecast_log SET status = 'censored' WHERE id = ?", (r["id"],))
                n += 1
            elif hit:
                c.execute("UPDATE forecast_log SET status = 'ok', actual_h = ? WHERE id = ?",
                          ((hit - r["ts"]) / 3600, r["id"]))
                n += 1
    return n


def actual_error(db: Database, days: float = WINDOW_DAYS) -> dict:
    """MAE фактической ошибки времени до порога за последние days суток."""
    with db.conn() as c:
        rows = c.execute("SELECT predicted_h, actual_h FROM forecast_log WHERE status = 'ok' "
                         "AND ts >= ?", (time.time() - days * 86400,)).fetchall()
        pending = c.execute("SELECT COUNT(*) AS n FROM forecast_log WHERE status IS NULL"
                            ).fetchone()["n"]
    errs = [abs(r["predicted_h"] - r["actual_h"]) for r in rows]
    return {"days": days, "checked": len(errs), "pending": pending,
            "ttt_mae_h": round(sum(errs) / len(errs), 2) if errs else None}


def on_telemetry(db: Database, device: str) -> None:
    resolve(db, device)
    log_forecast(db, device)


# ----- ежедневное переобучение -----

def retrain(db: Database, models_dir: str) -> dict | None:
    """Обучить на открытых данных + своих (если ≥ 3 циклов) и зарегистрировать по правилу 6.6."""
    from .train import open_data, train, with_own_data
    try:
        data, trained_on = open_data()
    except RuntimeError as e:
        log.warning("переобучение пропущено: %s", e)
        return None
    data, trained_on = with_own_data(db, data, trained_on)
    result = train(db, models_dir, data, trained_on)
    log.info("переобучение: версия %s, рабочая: %s", result["version"], result["is_champion"])
    return result


def start_scheduler(db: Database, models_dir: str, every_h: float) -> threading.Thread:
    def loop():
        while True:
            time.sleep(every_h * 3600)
            try:
                retrain(db, models_dir)
            except Exception:
                log.exception("ошибка переобучения")
    t = threading.Thread(target=loop, daemon=True, name="ml-retrain")
    t.start()
    return t
