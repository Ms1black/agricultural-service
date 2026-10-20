"""Калибровка под растение (C1). Логика только на сервере; устройство лишь выполняет поливы.

Этапы: 0 — профиль растения, 1 — две точки датчика, 2 — доза полива (k_dose),
3 — скорость высыхания (dry_rate), 4 — готово (дальше — адаптация после каждого полива).
Состояние автомата — в calibration_state (JSON), параметры — в calibration (контракт 4.5).
Автомат продвигается с каждой телеметрией (on_telemetry); любой импульс — через app.safety.
"""

import json
import statistics
import time

import numpy as np

from . import safety, store
from .db import Database

PROFILES = {
    "hygrophilous": ("Влаголюбивые", 50, 80),
    "moderate": ("Умеренные", 35, 65),
    "xerophytic": ("Засухоустойчивые", 10, 40),
}
DRY_WINDOW_S = 10 * 60          # «сухо»: медиана raw за 10 мин
DRY_MAX_SPREAD = 150            # разброс raw (макс − мин) за окно, иначе «подождите»
MIN_POINTS = 8
WET_WAIT_S = 30 * 60            # после пролива ждать 30 мин
PLATEAU_S = 10 * 60             # плато: 10 мин без значимых изменений
PLATEAU_RAW = 60                # «значимо» для raw
PLATEAU_PCT = 2.0               # «значимо» для %
MIN_RAW_SPAN = 500              # raw_dry − raw_wet, иначе «переставьте датчик»
PULSE_S = 2
MAX_PULSES = 4
MIN_PULSES = 2
PULSE_MIN_WAIT_S = 5 * 60       # раньше плато не ищем
PULSE_TIMEOUT_S = 60 * 60       # прироста нет за час — аномалия
RESPONSE_PCT = 2.0              # отклик: рост на 2 п. п.
DRY_FIT_MIN_S = 6 * 3600        # скорость высыхания — по участку ≥ 6 ч
EMA = 0.2                       # адаптация k_dose = 0.8·k + 0.2·k_последний
RAW_WET_DRIFT = 100             # плато после пролива влажнее raw_wet на столько — обновить
CALIB_NOTE = "calib"


# ----- хранение -----

def get(db: Database, device: str) -> dict:
    cal = store.get_calibration(db, device) or {}
    with db.conn() as c:
        row = c.execute("SELECT state_json FROM calibration_state WHERE device = ?",
                        (device,)).fetchone()
    cal["state"] = json.loads(row["state_json"]) if row else {}
    cal.setdefault("stage", 0)
    cal["stage"] = cal["stage"] or 0
    return cal


def save(db: Database, device: str, fields: dict | None = None, state: dict | None = None):
    now = time.time()
    with db.conn() as c:
        if fields:
            if "raw_dry" in fields:
                fields["dry_raw"] = fields["raw_dry"]
            if "raw_wet" in fields:
                fields["wet_raw"] = fields["raw_wet"]
            exists = c.execute("SELECT 1 FROM calibration WHERE device = ?", (device,)).fetchone()
            if not exists:
                c.execute("INSERT INTO calibration (device, dry_raw, wet_raw, updated, stage, "
                          "confidence, cycles) VALUES (?, ?, ?, ?, 0, 'none', 0)",
                          (device, fields.get("dry_raw", 4000), fields.get("wet_raw", 1150), now))
            cols = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE calibration SET {cols}, updated = ? WHERE device = ?",
                      (*fields.values(), now, device))
        if state is not None:
            c.execute("INSERT OR REPLACE INTO calibration_state (device, state_json, updated) "
                      "VALUES (?, ?, ?)", (device, json.dumps(state), now))


def percent(raw: float, raw_dry: float, raw_wet: float) -> int:
    """% = (raw_dry − raw) / (raw_dry − raw_wet) · 100, в пределах 0…100 (как в прошивке)."""
    if raw_dry == raw_wet:
        return 0
    return int(max(0, min(100, round((raw_dry - raw) / (raw_dry - raw_wet) * 100))))


def confidence(cycles: int, stage: int) -> str:
    if stage < 4:
        return "none" if stage < 3 else "low"
    return "low" if cycles < 3 else "medium" if cycles < 6 else "high"


def dose_seconds(db: Database, device: str, current_pct: float) -> int | None:
    """Длительность полива = (target_high − текущая) / k_dose, не больше 10 с."""
    cal = store.get_calibration(db, device)
    if not cal or not cal.get("k_dose") or cal.get("target_high") is None:
        return None
    target = min(cal["target_high"], safety.MAX_SOIL_PCT)
    return int(max(1, min(safety.MAX_WATER_SECONDS,
                          round((target - current_pct) / cal["k_dose"]))))


def _window(db: Database, device: str, since: float) -> list[dict]:
    """Показания с since; pct — дробные проценты из raw по текущей калибровке (точнее целых)."""
    cal = store.get_calibration(db, device)
    rows = [r for r in store.measurements_since(db, device, since)
            if r["soil_raw"] is not None and r["soil_ok"]]
    for r in rows:
        if cal and cal["dry_raw"] != cal["wet_raw"]:
            r["pct"] = max(0.0, min(100.0, (cal["dry_raw"] - r["soil_raw"]) /
                                    (cal["dry_raw"] - cal["wet_raw"]) * 100))
        else:
            r["pct"] = r["soil_pct"]
    return rows


# ----- этап 0: профиль -----

def set_profile(db: Database, device: str, plant_type: str, low: float | None = None,
                high: float | None = None) -> dict:
    if plant_type not in PROFILES:
        raise ValueError("тип растения: " + ", ".join(PROFILES))
    _, p_low, p_high = PROFILES[plant_type]
    low, high = (p_low if low is None else low), (p_high if high is None else high)
    if not 0 <= low < high <= 100:
        raise ValueError("нужно 0 ≤ нижняя граница < верхняя ≤ 100")
    cal = get(db, device)
    save(db, device, {"plant_type": plant_type, "target_low": low, "target_high": high,
                      "stage": max(cal["stage"], 1), "confidence": cal.get("confidence") or "none",
                      "cycles": cal.get("cycles") or 0}, cal["state"])
    return get(db, device)


# ----- этап 1: две точки -----

def mark_dry(db: Database, device: str, now: float | None = None) -> dict:
    """«Сухо» — медиана raw за 10 мин при малом разбросе."""
    now = now or time.time()
    rows = _window(db, device, now - DRY_WINDOW_S)
    if len(rows) < MIN_POINTS:
        raise ValueError("подождите: нужно 10 минут показаний датчика")
    raws = [r["soil_raw"] for r in rows]
    if max(raws) - min(raws) >= DRY_MAX_SPREAD:
        raise ValueError("подождите: показания ещё меняются")
    cal = get(db, device)
    state = {**cal["state"], "dry_done": True}
    save(db, device, {"raw_dry": int(statistics.median(raws))}, state)
    return get(db, device)


def start_wet(db: Database, device: str, now: float | None = None) -> dict:
    """Пролили полностью — через 30 мин ищем плато, его медиана = raw_wet."""
    cal = get(db, device)
    if not cal["state"].get("dry_done"):
        raise ValueError("сначала отметьте «сухо»")
    save(db, device, None, {**cal["state"], "wet_started": now or time.time()})
    return get(db, device)


def _plateau(rows: list[dict], key: str, delta: float, now: float) -> float | None:
    """Медиана за последние PLATEAU_S, если ряд всё это время стабилен."""
    win = [r[key] for r in rows if r["ts"] >= now - PLATEAU_S and r[key] is not None]
    if len(win) < MIN_POINTS or max(win) - min(win) > delta:
        return None
    return float(statistics.median(win))


def _step_wet(db, device, cal, now) -> str | None:
    st = cal["state"]
    if now - st["wet_started"] < WET_WAIT_S:
        return None
    wet = _plateau(_window(db, device, now - PLATEAU_S), "soil_raw", PLATEAU_RAW, now)
    if wet is None:
        return None
    st = {k: v for k, v in st.items() if k != "wet_started"}
    if cal["raw_dry"] - wet < MIN_RAW_SPAN:
        save(db, device, None, {**st, "dry_done": False,
                                "error": "переставьте датчик: сухо и влажно почти не отличаются"})
        return "error"
    save(db, device, {"raw_wet": int(wet), "stage": 2},
         {"error": None, "pulses": [], "dry_done": True})
    return "wet"


# ----- этап 2: доза -----

def _soil_now(db, device, now, span=5 * 60) -> float | None:
    rows = [r for r in _window(db, device, now - span) if r["pct"] is not None]
    return float(statistics.median(r["pct"] for r in rows)) if rows else None


def _slope(db, device, now, span=30 * 60) -> float:
    """Скорость изменения влажности за последние 30 мин, %/ч (отрицательная — сохнет)."""
    rows = [r for r in _window(db, device, now - span) if r["pct"] is not None]
    if len(rows) < MIN_POINTS:
        return 0.0
    t = np.array([(r["ts"] - now) / 3600 for r in rows])
    slope = float(np.polyfit(t, [r["pct"] for r in rows], 1)[0])
    return min(0.0, slope)


def _measure_pulse(db, device, pulse, now) -> dict | None:
    """Плато после импульса: прирост Δ%, задержка отклика. None — ещё рано."""
    if now - pulse["ts"] < PULSE_MIN_WAIT_S:
        return None
    rows = [r for r in _window(db, device, pulse["ts"]) if r["pct"] is not None]
    plateau = _plateau(rows, "pct", PLATEAU_PCT, now)
    late = now - pulse["ts"] > PULSE_TIMEOUT_S
    if plateau is None and not late:
        return None
    level = plateau if plateau is not None else _soil_now(db, device, now)
    # Пока ждём плато, почва продолжает сохнуть — учитываем скорость до импульса
    elapsed_h = (now - PLATEAU_S / 2 - pulse["ts"]) / 3600
    baseline = pulse["before"] + pulse.get("slope", 0.0) * elapsed_h
    gain = (level or pulse["before"]) - baseline
    rise = next((r["ts"] for r in rows if r["pct"] >= pulse["before"] + RESPONSE_PCT), None)
    return {"gain": round(gain, 2), "delay_min": round((rise - pulse["ts"]) / 60, 1)
            if rise else None, "k": round(gain / pulse["seconds"], 3), "anomaly":
            gain < RESPONSE_PCT}


def _top(cal: dict) -> float:
    """Верхняя граница для полива: выше 70 % поливать запрещено (раздел 5)."""
    return min(cal["target_high"], safety.MAX_SOIL_PCT)


def _step_dose(db, device, cal, now) -> str | None:
    st = cal["state"]
    pulse = st.get("pulse")
    if pulse:
        m = _measure_pulse(db, device, pulse, now)
        if m is None:
            return None
        if m["anomaly"]:
            return _anomaly(db, device, cal, "импульс полива не поднял влажность", now)
        pulses = [*st.get("pulses", []), {**pulse, **m}]
        st = {**st, "pulse": None, "pulses": pulses}
        soil = _soil_now(db, device, now) or 0
        if len(pulses) >= MAX_PULSES or (len(pulses) >= MIN_PULSES and soil >= _top(cal) - 2):
            delays = [p["delay_min"] for p in pulses if p["delay_min"] is not None]
            save(db, device, {"k_dose": round(float(np.mean([p["k"] for p in pulses])), 3),
                              "response_delay_min": round(float(np.mean(delays)), 1)
                              if delays else None, "stage": 3},
                 {**st, "dry_from": now})
            return "dose"
        save(db, device, None, st)
        return "pulse_measured"
    soil = _soil_now(db, device, now)
    mid = (cal["target_low"] + cal["target_high"]) / 2
    if soil is None or (soil >= mid and not st.get("pulses")):
        return None                      # ждём, пока почва подсохнет ниже середины диапазона
    if soil >= _top(cal) - 2:
        return None
    v = safety.request_water(db, device, PULSE_S, CALIB_NOTE, now)
    if v.ok:
        save(db, device, None, {**st, "pulse": {"ts": now, "before": soil, "seconds": v.seconds,
                                                "slope": _slope(db, device, now)}})
        return "pulse"
    return None


# ----- этап 3: скорость высыхания -----

def fit_dry_rate(rows: list[dict]) -> float | None:
    """%/ч по линейной аппроксимации soil ~ t + темп. + влажн. воздуха (поправки центрированы)."""
    rows = [{**r, "pct": r.get("pct", r["soil_pct"])} for r in rows]
    rows = [r for r in rows if r["pct"] is not None]
    if len(rows) < MIN_POINTS or rows[-1]["ts"] - rows[0]["ts"] < DRY_FIT_MIN_S:
        return None
    t = np.array([(r["ts"] - rows[0]["ts"]) / 3600 for r in rows])
    cols = [np.ones_like(t), t]
    for key in ("temp", "air_hum"):
        v = np.array([r[key] if r[key] is not None else np.nan for r in rows], float)
        if np.isfinite(v).all() and v.std() > 1e-6:
            cols.append(v - v.mean())
    x = np.column_stack(cols)
    y = np.array([r["pct"] for r in rows], float)
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    rate = -coef[1]
    return round(float(rate), 3) if rate > 0 else None


def _step_dry(db, device, cal, now) -> str | None:
    st = cal["state"]
    since = st.get("dry_from") or now
    # Участок высыхания — после плато последнего полива, до следующего полива
    rows = _window(db, device, since + PLATEAU_S)
    if _watered_since(db, device, since + 60):
        save(db, device, None, {**st, "dry_from": now})
        return None
    rate = fit_dry_rate(rows)
    if rate is None:
        return None
    save(db, device, {"dry_rate": rate, "stage": 4, "confidence": confidence(0, 4),
                      "cycles": 0}, {**st, "last_water": None, "adapted_until": now})
    return "dry_rate"


def _watered_since(db, device, since) -> bool:
    with db.conn() as c:
        return c.execute("SELECT 1 FROM events WHERE device = ? AND type = 'water' AND ts > ?",
                         (device, since)).fetchone() is not None


# ----- адаптация после каждого полива -----

def _step_adapt(db, device, cal, now) -> str | None:
    st = cal["state"]
    pending = st.get("last_water")
    if pending is None:
        with db.conn() as c:
            row = c.execute("SELECT ts, value FROM events WHERE device = ? AND type = 'water' "
                            "AND ts > ? ORDER BY ts DESC LIMIT 1",
                            (device, st.get("adapted_until") or now)).fetchone()
        if not row or not row["value"]:
            return None
        before = _soil_before(db, device, row["ts"])
        if before is None:
            save(db, device, None, {**st, "adapted_until": row["ts"]})
            return None
        save(db, device, None, {**st, "last_water": {
            "ts": row["ts"], "before": before, "seconds": row["value"],
            "slope": _slope(db, device, row["ts"])}})
        return None
    m = _measure_pulse(db, device, pending, now)
    if m is None:
        return None
    st = {**st, "last_water": None, "adapted_until": pending["ts"]}
    if m["anomaly"]:
        save(db, device, None, st)
        return _anomaly(db, device, get(db, device), "полив был, а влажность не выросла", now)
    k = round((1 - EMA) * cal["k_dose"] + EMA * m["k"], 3)
    fields = {"k_dose": k, "cycles": (cal["cycles"] or 0) + 1}
    raw_plateau = _plateau(_window(db, device, now - PLATEAU_S), "soil_raw", PLATEAU_RAW, now)
    if raw_plateau is not None and raw_plateau < cal["raw_wet"] - RAW_WET_DRIFT:
        fields["raw_wet"] = int(round((1 - EMA) * cal["raw_wet"] + EMA * raw_plateau))
    fields["confidence"] = confidence(fields["cycles"], cal["stage"])
    save(db, device, fields, st)
    return "adapted"


def _soil_before(db, device, ts) -> float | None:
    rows = [r for r in _window(db, device, ts - 5 * 60) if r["ts"] <= ts and
            r["pct"] is not None]
    return float(statistics.median(r["pct"] for r in rows)) if rows else None


# ----- аномалии -----

def _anomaly(db, device, cal, text, now) -> str:
    store.save_event(db, device, "anomaly", None, text)
    save(db, device, None, {**cal["state"], "pulse": None, "anomaly": {"ts": now, "text": text}})
    return "anomaly"


def anomaly_active(db: Database, device: str) -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT state_json FROM calibration_state WHERE device = ?",
                        (device,)).fetchone()
    return json.loads(row["state_json"]).get("anomaly") if row else None


def confirm_anomaly(db: Database, device: str) -> dict:
    cal = get(db, device)
    save(db, device, None, {**cal["state"], "anomaly": None})
    store.save_event(db, device, "anomaly_confirmed", None, "web")
    return get(db, device)


def reset(db: Database, device: str) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM calibration_state WHERE device = ?", (device,))
        c.execute("UPDATE calibration SET stage = 0, plant_type = NULL, target_low = NULL, "
                  "target_high = NULL, k_dose = NULL, response_delay_min = NULL, "
                  "dry_rate = NULL, confidence = 'none', cycles = 0 WHERE device = ?", (device,))


# ----- шаг автомата -----

def on_telemetry(db: Database, device: str, now: float | None = None) -> str | None:
    """Продвинуть калибровку по свежим данным. Возвращает, что произошло (для журнала/тестов)."""
    now = now or time.time()
    cal = get(db, device)
    st = cal["state"]
    if st.get("anomaly"):
        return None
    if st.get("wet_started"):
        return _step_wet(db, device, cal, now)
    if cal["stage"] == 2:
        return _step_dose(db, device, cal, now)
    if cal["stage"] == 3:
        return _step_dry(db, device, cal, now)
    if cal["stage"] >= 4 and cal.get("k_dose"):
        return _step_adapt(db, device, cal, now)
    return None


def hint(cal: dict) -> str:
    """Подсказка мастера калибровки."""
    st, stage = cal["state"], cal["stage"]
    if st.get("anomaly"):
        return ("Аномалия: " + st["anomaly"]["text"] + ". Проверьте насос и воду, затем "
                "подтвердите — до этого сервер не поливает.")
    if st.get("error"):
        return "Ошибка: " + st["error"] + "."
    if stage == 0:
        return "Шаг 0: выберите тип растения — от него зависит целевой диапазон влажности."
    if stage == 1 and not st.get("dry_done"):
        return ("Шаг 1: вставьте датчик в сухую землю, подождите 10 минут и нажмите «Сухо».")
    if stage == 1 and st.get("wet_started"):
        return ("Шаг 1: ждём, пока вода распределится (30 мин) и показания перестанут "
                "меняться (10 мин).")
    if stage == 1:
        return "Шаг 1: полностью пролейте горшок до вытекания воды и нажмите «Пролил»."
    if stage == 2:
        n = len(st.get("pulses", []))
        if st.get("pulse"):
            return f"Шаг 2: импульс {n + 1} — 2 с, ждём, пока влажность перестанет расти."
        return (f"Шаг 2: измеряем дозу. Импульсов: {n} из 2–4. Следующий — когда почва "
                "подсохнет ниже середины диапазона.")
    if stage == 3:
        return "Шаг 3: измеряем скорость высыхания — нужно хотя бы 6 часов без полива."
    return "Калибровка завершена. Параметры уточняются после каждого полива."
