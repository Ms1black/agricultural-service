"""Метрики оценки (раздел 6 PLAN.md). Чистые функции на numpy — тестируются на синтетике."""

import numpy as np

# Прогноз «не дойдёт до порога» ограничиваем двумя неделями
TTT_CAP_H = 24 * 14.0


def mae(y_true, y_pred) -> float:
    y_true, y_pred = np.asarray(y_true, float), np.asarray(y_pred, float)
    ok = ~(np.isnan(y_true) | np.isnan(y_pred))
    return float(np.mean(np.abs(y_true[ok] - y_pred[ok]))) if ok.any() else float("nan")


def time_to_threshold(soil, rate_per_h, threshold: float) -> np.ndarray:
    """Часы до порога при линейном высыхании; уже ниже порога — 0; не сохнет — TTT_CAP_H."""
    soil, rate = np.asarray(soil, float), np.asarray(rate_per_h, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        hours = np.where(rate < 0, (soil - threshold) / -rate, TTT_CAP_H)
    hours = np.where(soil <= threshold, 0.0, hours)
    return np.clip(hours, 0, TTT_CAP_H)


def ttt_errors(true_h, pred_h, tolerance_h: float = 3.0) -> dict:
    """6.1: MAE и медиана ошибки времени до порога (ч), доля прогнозов в пределах ±tolerance_h."""
    true_h, pred_h = np.asarray(true_h, float), np.asarray(pred_h, float)
    ok = ~(np.isnan(true_h) | np.isnan(pred_h))
    err = np.abs(np.minimum(pred_h[ok], TTT_CAP_H) - true_h[ok])
    if not len(err):
        return {"ttt_mae_h": None, "ttt_median_h": None, "ttt_within_3h": None, "n": 0}
    return {"ttt_mae_h": round(float(err.mean()), 2),
            "ttt_median_h": round(float(np.median(err)), 2),
            "ttt_within_3h": round(float((err <= tolerance_h).mean()), 3), "n": int(len(err))}


def improvement(model_err: float, baseline_err: float) -> float:
    """На сколько модель лучше бейзлайна (доля; > 0 — лучше)."""
    if not baseline_err:
        return 0.0
    return round(1 - model_err / baseline_err, 3)


def beats(challenger: dict, champion: dict, margin: float = 0.05) -> bool:
    """6.6: ошибка времени до порога лучше минимум на margin и MAE на 6 ч не хуже."""
    c, r = challenger.get("ttt_mae_h"), champion.get("ttt_mae_h")
    if c is None:
        return False
    if r is None:
        return True
    mae6_ok = challenger.get("mae_6h", 0) <= champion.get("mae_6h", float("inf"))
    return c <= r * (1 - margin) and mae6_ok
