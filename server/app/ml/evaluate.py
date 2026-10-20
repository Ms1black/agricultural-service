"""Оценка прогноза (6.1, 6.3): разбиение по времени, leave-one-source-out, кривая адаптации."""

import numpy as np
import pandas as pd

from . import model as fm
from .metrics import improvement, mae, ttt_errors

TRAIN_SHARE = 0.7
ADAPT_MAX_CYCLES = 5
ADAPT_MIN_CYCLES = 7


def split_by_time(data: pd.DataFrame, share: float = TRAIN_SHARE) -> tuple[pd.DataFrame,
                                                                           pd.DataFrame]:
    """Каждый ряд: первые share по времени — обучение, остальное — проверка."""
    cut = data.groupby("series")["ts"].transform(lambda t: t.quantile(share))
    return data[data["ts"] <= cut], data[data["ts"] > cut]


def score(model: fm.ForecastModel | None, test: pd.DataFrame, scale: float = 1.0) -> dict:
    """Метрики 6.1 для модели (или бейзлайнов, если model=None)."""
    out = {"n": int(len(test))}
    preds = {"persistence": fm.persistence(test), "linear": fm.linear(test)}
    if model is not None:
        preds["model"] = model.predict(test, scale)
    for name, p in preds.items():
        for h in fm.HORIZONS:
            out[f"{name}_mae_{h}h"] = round(mae(test[f"d{h}"], p[h]), 3)
    with_ttt = fm.ttt_rows(test)
    ttt = {"linear": fm.linear_ttt(with_ttt)}
    if model is not None:
        ttt["mean_rate"] = fm.mean_rate_ttt(with_ttt, model.mean_rate)
        ttt["model"] = model.ttt(with_ttt, scale)
    for name, p in ttt.items():
        for k, v in ttt_errors(with_ttt["ttt_true"], p).items():
            out[f"{name}_{k}"] = v
    if model is not None:
        out["gain_ttt_vs_linear"] = improvement(out["model_ttt_mae_h"] or 0,
                                                out["linear_ttt_mae_h"] or 0)
        out["gain_mae_6h_vs_linear"] = improvement(out["model_mae_6h"], out["linear_mae_6h"])
    return out


def time_split(data: pd.DataFrame, kind: str = "gbr") -> dict:
    train, test = split_by_time(data)
    model = fm.ForecastModel(kind).fit(train)
    return score(model, test)


def leave_one_source_out(data: pd.DataFrame, kind: str = "gbr") -> dict:
    """Обучение на всех источниках, кроме одного; проверка — на нём (вторая половина по времени)."""
    out = {}
    for src in sorted(data["source"].unique()):
        train = data[data["source"] != src]
        _, test = split_by_time(data[data["source"] == src], 0.5)
        if train.empty or test.empty:
            continue
        out[src] = score(fm.ForecastModel(kind).fit(train), test)
    return out


def adaptation_curve(data: pd.DataFrame, kind: str = "gbr") -> dict:
    """6.3: ошибка времени до порога до адаптации и после 1…5 своих циклов.

    «Своё растение» — ряд, не участвовавший в обучении (обучение — на остальных рядах).
    Первые ADAPT_MAX_CYCLES циклов — для адаптации, остальные — для проверки.
    """
    curves = {}
    for series, own in data.groupby("series"):
        # Циклы, в которых почва дошла до порога (по ним можно и адаптировать, и проверять)
        cyc = sorted(fm.ttt_rows(own)["cycle"].unique())
        if len(cyc) < ADAPT_MIN_CYCLES:
            continue
        model = fm.ForecastModel(kind).fit(data[data["series"] != series])
        test = fm.ttt_rows(own[own["cycle"].isin(cyc[ADAPT_MAX_CYCLES:])])
        points = []
        for k in range(ADAPT_MAX_CYCLES + 1):
            scale = fm.fit_scale(model, own[own["cycle"].isin(cyc[:k])]) if k else 1.0
            err = ttt_errors(test["ttt_true"], model.ttt(test, scale))
            points.append({"cycles": k, "scale": round(scale, 3), "ttt_mae_h": err["ttt_mae_h"]})
        curves[series] = points
    if curves:
        mean = []
        for k in range(ADAPT_MAX_CYCLES + 1):
            vals = [c[k]["ttt_mae_h"] for c in curves.values() if c[k]["ttt_mae_h"] is not None]
            avg = round(float(np.mean(vals)), 2) if vals else None
            mean.append({"cycles": k, "ttt_mae_h": avg})
        curves["_mean"] = mean
    return curves
