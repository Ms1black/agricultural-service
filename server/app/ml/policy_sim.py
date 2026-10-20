"""Оценка политики полива (6.4): офлайн-симуляция на исторических данных, threshold против ml.

Высыхание берём из реального ряда (скорость на каждом шаге — как в данных), поливы — по политике,
отклик на полив — доза k_dose. Так обе политики проходят через одну и ту же «погоду».
threshold — как эталонная прошивка: ниже 30 % — 3 с, пауза 60 с, без суточного лимита.
ml — по прогнозу времени до нижней границы и дозе до верхней цели (как D0), с лимитами
раздела 5 для решений сервера (≤ 10 с, ≥ 30 мин между поливами, ≤ 6 в сутки, не выше 70 %).
"""

import numpy as np
import pandas as pd

from . import model as fm

LOW, HIGH = 30.0, 70.0          # целевой диапазон (6.4)
TARGET = 65.0                   # ml доливает до верхней цели с запасом от 70 %
K_DOSE = 4.0                    # % за секунду насоса (типично для горшка 0.5–1 л)
THRESHOLD_SECONDS = 3           # эталонная прошивка: WATER_MS = 3000
MIN_INTERVAL_H = 0.5            # ml: раздел 5
MAX_PER_DAY = 6
DEVICE_COOLDOWN_H = 1 / 60      # threshold: COOLDOWN_MS прошивки
DECIDE_EVERY_H = 1.0
LEAD_H = 1.0


def drying_rates(df: pd.DataFrame) -> np.ndarray:
    """Скорость высыхания (%/ч, ≥ 0) на каждом шаге реального ряда; поливы и рост — медианой."""
    step_h = df["ts"].diff().median() / pd.Timedelta("1h")
    d = -df["soil"].diff() / step_h
    d[df["watering_event"] | (d < 0) | d.isna()] = np.nan
    d = d.rolling(max(1, round(1 / step_h)), min_periods=1, center=True).median()
    return d.fillna(d.median()).clip(lower=0).to_numpy()


def simulate(df: pd.DataFrame, policy: str, model: fm.ForecastModel | None = None,
             k_dose: float = K_DOSE, start: float = 60.0) -> dict:
    step_h = df["ts"].diff().median() / pd.Timedelta("1h")
    rates = drying_rates(df)
    temp = df["temp"].fillna(fm.NEUTRAL["temp"]).to_numpy()
    hum = df["air_hum"].fillna(fm.NEUTRAL["air_hum"]).to_numpy()
    hours = (df["ts"].dt.hour + df["ts"].dt.minute / 60).to_numpy()
    n = len(df)
    soil = np.empty(n)
    s, last_water, waterings, pump = start, -1e9, [], 0.0
    every = max(1, round(DECIDE_EVERY_H / step_h))
    k3, k6 = max(1, round(3 / step_h)), max(1, round(6 / step_h))
    for i in range(n):
        t = i * step_h
        s = max(0.0, s - rates[i] * step_h)
        soil[i] = s
        seconds = 0
        if policy == "threshold":
            if s < LOW and t - last_water >= DEVICE_COOLDOWN_H:
                seconds = THRESHOLD_SECONDS
        elif (t - last_water < MIN_INTERVAL_H or s > HIGH
              or sum(1 for w in waterings if w > t - 24) >= MAX_PER_DAY):
            pass
        elif i % every == 0 and i >= k6:
            row = pd.DataFrame([{
                "soil": s, "rate_3h": (s - soil[i - k3]) / (k3 * step_h),
                "rate_6h": (s - soil[i - k6]) / (k6 * step_h),
                "hours_since_water": min(t - last_water, 24 * 14.0), "temp": temp[i],
                "air_hum": hum[i], "hour_sin": np.sin(2 * np.pi * hours[i] / 24),
                "hour_cos": np.cos(2 * np.pi * hours[i] / 24), "threshold": LOW}])
            if model is not None:
                ttt = float(model.ttt(row)[0])
            else:
                rate = min(row["rate_6h"].iloc[0], -1e-3)
                ttt = max(0.0, (s - LOW) / -rate)
            if ttt <= LEAD_H or s < LOW:
                seconds = int(np.clip(round((TARGET - s) / k_dose), 1, 10))
        if seconds:
            s = min(100.0, s + k_dose * seconds)
            soil[i] = s
            last_water = t
            waterings.append(t)
            pump += seconds
    days = n * step_h / 24
    return {"policy": policy, "days": round(days, 1),
            "in_range_share": round(float(((soil >= LOW) & (soil <= HIGH)).mean()), 3),
            "hours_dry": round(float((soil < LOW).sum() * step_h), 1),
            "hours_wet": round(float((soil > HIGH).sum() * step_h), 1),
            "mean_soil": round(float(soil.mean()), 1),
            "waterings": len(waterings), "pump_seconds": round(pump, 1),
            "waterings_per_day": round(len(waterings) / days, 2) if days else 0}


def compare(series: list[pd.DataFrame], model: fm.ForecastModel | None,
            max_days: float = 60) -> dict:
    """Обе политики на каждом ряду (не длиннее max_days) + суммарно."""
    out = {"per_series": {}, "total": {}}
    for df in series:
        step_h = df["ts"].diff().median() / pd.Timedelta("1h")
        df = df.iloc[-int(max_days * 24 / step_h):].reset_index(drop=True)
        name = str(df["series"].iloc[0])
        out["per_series"][name] = {p: simulate(df, p, model if p == "ml" else None)
                                   for p in ("threshold", "ml")}
    for p in ("threshold", "ml"):
        runs = [r[p] for r in out["per_series"].values()]
        days = sum(r["days"] for r in runs) or 1
        out["total"][p] = {
            "days": round(days, 1),
            "in_range_share": round(sum(r["in_range_share"] * r["days"] for r in runs) / days, 3),
            "hours_dry": round(sum(r["hours_dry"] for r in runs), 1),
            "hours_wet": round(sum(r["hours_wet"] for r in runs), 1),
            "mean_soil": round(sum(r["mean_soil"] * r["days"] for r in runs) / days, 1),
            "waterings": sum(r["waterings"] for r in runs),
            "pump_seconds": round(sum(r["pump_seconds"] for r in runs), 1),
        }
    return out
