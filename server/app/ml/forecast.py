"""Прогноз для устройства: рабочая модель + адаптация по своим циклам; без модели — бейзлайн."""

import time

import numpy as np
import pandas as pd

from .. import store
from ..db import Database
from . import model as fm
from . import registry
from .datasets import frame
from .metrics import TTT_CAP_H
from .prepare import add_features, to_grid

HISTORY_DAYS = 30
MIN_HOURS = 6           # меньше — нет скорости за 6 ч, прогноз не строим
DEFAULT_RATE = 0.5      # п. п./ч, если не из чего оценить


def device_frame(db: Database, device: str, now: float) -> pd.DataFrame | None:
    """Ряд устройства на 15-минутной сетке; soil — проценты устройства (уже откалиброваны)."""
    rows = store.measurements_since(db, device, now - HISTORY_DAYS * 86400)
    rows = [r for r in rows if r["soil_ok"] and r["soil_pct"] is not None]
    if len(rows) < 2:
        return None
    m = pd.DataFrame(rows)
    raw = frame(pd.to_datetime(m["ts"], unit="s"), "device", device, temp=m["temp"],
                air_hum=m["air_hum"], soil_raw=m["soil_raw"], soil_pct=m["soil_pct"],
                watering=[False] * len(m))
    df = to_grid(raw, pd.Timedelta("15min"))
    with db.conn() as c:
        water = c.execute("SELECT ts FROM events WHERE device = ? AND type = 'water' AND ts >= ?",
                          (device, now - HISTORY_DAYS * 86400)).fetchall()
    for (ts,) in water:
        t = pd.Timestamp(ts, unit="s", tz="UTC").floor("15min")
        df.loc[df["ts"] == t, "watering_event"] = True
    df["soil"] = df["soil_pct"]
    return add_features(df)


def forecast(db: Database, device: str, threshold: float = fm.DEFAULT_THRESHOLD,
             now: float | None = None) -> dict:
    now = now or time.time()
    entry = registry.champion(db, registry.TASK_DRYING)
    info = ({"version": entry["version"], "kind": entry["metrics"].get("kind"),
             "trained_on": entry["trained_on"],
             "metrics": {k: entry["metrics"].get(k) for k in (
                 "mae_6h", "mae_12h", "mae_24h", "ttt_mae_h", "ttt_median_h", "ttt_within_3h",
                 "gain_ttt_vs_linear", "baselines", "adaptation")}} if entry else None)
    out = {"device": device, "now": now, "threshold": threshold, "model": info}
    df = device_frame(db, device, now)
    if df is None or (df["ts"].iloc[-1] - df["ts"].iloc[0]) < pd.Timedelta(hours=MIN_HOURS):
        return {**out, "status": "insufficient_data",
                "note": f"недостаточно данных: нужно хотя бы {MIN_HOURS} ч показаний"}
    last = df.dropna(subset=["soil"]).iloc[[-1]].copy()
    if pd.isna(last["rate_6h"].iloc[0]):
        last["rate_6h"] = last["rate_3h"].fillna(0)
    last["threshold"] = threshold
    soil = float(last["soil"].iloc[0])
    own = fm.samples(df, threshold)
    own_cycles = int(fm.ttt_rows(own)["cycle"].nunique())

    if entry:
        model: fm.ForecastModel = registry.load(entry)
        scale = fm.fit_scale(model, own) if own_cycles else 1.0
        deltas = {h: float(v[0]) for h, v in model.predict(last, scale).items()}
        hours = float(model.ttt(last, scale)[0])
        band = model.residuals
        method = "model"
    else:
        rate = float(last["rate_6h"].iloc[0])
        rate = rate if rate < 0 else -DEFAULT_RATE
        scale, deltas, band, method = 1.0, {h: rate * h for h in fm.HORIZONS}, {}, "linear"
        hours = float(np.clip((soil - threshold) / -rate, 0, TTT_CAP_H))

    trajectory = [{"h": 0, "soil": round(soil, 1), "lo": round(soil, 1), "hi": round(soil, 1)}]
    for h in fm.HORIZONS:
        q = band.get(h) or band.get(str(h)) or [0.0, 0.0]
        val = soil + deltas[h]
        trajectory.append({"h": h, "soil": round(float(np.clip(val, 0, 100)), 1),
                           "lo": round(float(np.clip(val + q[0], 0, 100)), 1),
                           "hi": round(float(np.clip(val + q[1], 0, 100)), 1)})
    hours = consistent_hours(trajectory, threshold, hours)
    note = None if method == "model" else "модели нет — линейная экстраполяция за 6 ч"
    return {**out, "status": "ok", "method": method, "soil": round(soil, 1),
            "hours_to_threshold": round(hours, 1),
            "next_water_ts": now + hours * 3600 if hours < TTT_CAP_H else None,
            "trajectory": trajectory,
            "adaptation": {"own_cycles": own_cycles, "scale": round(scale, 3)}, "note": note}


def consistent_hours(trajectory: list[dict], threshold: float, hours: float) -> float:
    """Время до порога, согласованное с траекторией: пересекает порог в 24 ч — берём точку
    пересечения, иначе — прогноз времени до порога, но не меньше горизонта траектории."""
    for a, b in zip(trajectory, trajectory[1:], strict=False):
        if a["soil"] > threshold >= b["soil"]:
            return a["h"] + (a["soil"] - threshold) / (a["soil"] - b["soil"]) * (b["h"] - a["h"])
    if trajectory[0]["soil"] <= threshold:
        return 0.0
    return max(hours, float(trajectory[-1]["h"]))


def describe(fc: dict) -> str:
    """Строка для контекста LLM."""
    if fc.get("status") != "ok":
        return "Прогноза пока нет: " + fc.get("note", "нет данных") + "."
    h = fc["hours_to_threshold"]
    when = "почва уже у порога полива" if h <= 0 else (
        f"до порога полива {fc['threshold']:g} % примерно {h:.0f} ч" if h < TTT_CAP_H
        else "в ближайшие две недели до порога не дойдёт")
    src = (f"модель {fc['model']['version']}, обучена на открытых датасетах"
           if fc["method"] == "model" else "простая экстраполяция (модели нет)")
    ad = fc["adaptation"]
    adapt = (f", адаптирована по {ad['own_cycles']} своим циклам (×{ad['scale']:g})"
             if ad["own_cycles"] else ", своих циклов для адаптации ещё нет")
    return f"Прогноз: {when} ({src}{adapt})."
