"""Открытый датасет как источник данных дашборда (W1) — для демонстрации, пока своих данных мало.

Ряд сдвигается во времени так, чтобы его конец совпал с «сейчас»; дашборд это подписывает.
"""

import threading
import time

import pandas as pd

from . import model as fm
from . import registry
from .datasets import data_dir, sources
from .prepare import prepare, quality

_cache: dict = {}
_lock = threading.Lock()


def series() -> dict[str, pd.DataFrame]:
    """Пригодные ряды открытых датасетов (кэш на процесс; пересчёт, если файлы обновились)."""
    root = data_dir()
    stamp = tuple(sorted((str(p), p.stat().st_mtime) for p in root.rglob("*") if p.is_file())) \
        if root.is_dir() else ()
    with _lock:
        if _cache.get("stamp") != stamp:
            out = {}
            for key, s in sources().items():
                if s.auxiliary or not (root / key).is_dir():
                    continue
                for raw in s.load(root / key):
                    df = prepare(raw)
                    if quality(raw, df).usable:
                        out[str(df["series"].iloc[0])] = df
            _cache.update(stamp=stamp, series=out)
        return _cache["series"]


def view(db, name: str, hours: float, now: float | None = None) -> dict:
    """История (сдвинута к «сейчас»), поливы и прогноз рабочей модели в последней точке."""
    now = now or time.time()
    all_series = series()
    if name not in all_series:
        raise KeyError(name)
    df = all_series[name].dropna(subset=["soil"])
    shift = now - df["ts"].iloc[-1].timestamp()
    part = df[df["ts"] >= df["ts"].iloc[-1] - pd.Timedelta(hours=hours)]
    num = lambda v: None if pd.isna(v) else round(float(v), 1)  # noqa: E731
    history = [{"ts": t.timestamp() + shift, "soil_pct": round(float(v), 1), "soil_ok": 1,
                "temp": num(tc), "air_hum": num(h)}
               for t, v, tc, h in zip(part["ts"], part["soil"], part["temp"], part["air_hum"],
                                      strict=False)]
    waterings = [{"ts": t.timestamp() + shift, "note": "inferred" if inf else "dataset"}
                 for t, inf in zip(part.loc[part["watering_event"], "ts"],
                                   part.loc[part["watering_event"], "watering_inferred"],
                                   strict=False)]
    out = {"series": name, "source": str(df["source"].iloc[0]), "shift_s": shift,
           "period": [f"{df['ts'].iloc[0]:%Y-%m-%d}", f"{df['ts'].iloc[-1]:%Y-%m-%d}"],
           "history": history, "waterings": waterings, "forecast": None}
    entry = registry.champion(db, registry.TASK_DRYING)
    if entry:
        model: fm.ForecastModel = registry.load(entry)
        last = df.iloc[[-1]].copy()
        last["threshold"] = fm.DEFAULT_THRESHOLD
        soil = float(last["soil"].iloc[0])
        deltas = {h: float(v[0]) for h, v in model.predict(last).items()}
        traj = [{"h": 0, "soil": round(soil, 1), "lo": round(soil, 1), "hi": round(soil, 1)}]
        for h in fm.HORIZONS:
            q = model.residuals.get(h) or [0.0, 0.0]
            v = soil + deltas[h]
            traj.append({"h": h, "soil": round(min(100, max(0, v)), 1),
                         "lo": round(min(100, max(0, v + q[0])), 1),
                         "hi": round(min(100, max(0, v + q[1])), 1)})
        from .forecast import consistent_hours
        hrs = consistent_hours(traj, fm.DEFAULT_THRESHOLD, float(model.ttt(last)[0]))
        out["forecast"] = {"status": "ok", "now": now, "trajectory": traj,
                           "hours_to_threshold": round(hrs, 1), "threshold": fm.DEFAULT_THRESHOLD}
    return out
