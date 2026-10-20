"""Прогноз высыхания (M1, задача 1): изменение влажности через 6/12/24 ч → время до порога.

Модель предсказывает изменение нормализованной влажности (п. п.) на горизонтах HORIZONS.
Время до порога — по прогнозу скорости на 24 ч. Адаптация под своё растение — множитель
скорости высыхания по первым собственным циклам (scale).
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .metrics import TTT_CAP_H, time_to_threshold

FEATURES = ["soil", "rate_3h", "rate_6h", "hours_since_water", "temp", "air_hum",
            "hour_sin", "hour_cos"]
HORIZONS = (6, 12, 24)
DEFAULT_THRESHOLD = 30.0
# Нет датчика воздуха в источнике — нейтральные значения (иначе признак целиком пустой)
NEUTRAL = {"temp": 22.0, "air_hum": 50.0}
SCALE_LIMITS = (0.1, 10.0)


def watering_threshold(df: pd.DataFrame) -> float:
    """Порог полива ряда: медиана минимума влажности за 3 ч до каждого полива."""
    step_h = df["ts"].diff().median() / pd.Timedelta("1h")
    k = max(1, round(3 / step_h))
    before = df["soil"].rolling(k, min_periods=1).min().shift(1)
    levels = before[df["watering_event"]].dropna()
    if len(levels) < 2:
        return DEFAULT_THRESHOLD
    return float(np.clip(levels.median(), 5, 60))


def true_time_to_threshold(df: pd.DataFrame, threshold: float) -> pd.Series:
    """Фактические часы до порога внутри текущего цикла; NaN — порог не достигнут до полива."""
    hours = (df["ts"] - df["ts"].iloc[0]) / pd.Timedelta("1h")
    t_below = hours.where(df["soil"] <= threshold)
    t_next = t_below.groupby(df["cycle"]).bfill()
    return (t_next - hours).where(df["soil"].notna())


def samples(df: pd.DataFrame, threshold: float | None = None) -> pd.DataFrame:
    """Обучающие примеры раз в час: признаки, цели d6/d12/d24 (в пределах цикла), время до порога.

    threshold=None — порог полива ряда по истории (открытые данные), иначе — заданный (устройство).
    """
    df = df.reset_index(drop=True)
    step_h = df["ts"].diff().median() / pd.Timedelta("1h")
    thr = watering_threshold(df) if threshold is None else threshold
    out = df[["ts", "source", "series", "cycle", *FEATURES]].copy()
    out["threshold"] = thr
    for h in HORIZONS:
        k = round(h / step_h)
        same_cycle = df["cycle"].shift(-k) == df["cycle"]
        out[f"d{h}"] = (df["soil"].shift(-k) - df["soil"]).where(same_cycle)
    out["ttt_true"] = true_time_to_threshold(df, thr)
    hourly = df["ts"].dt.minute == df["ts"].dt.minute.iloc[0]
    keep = hourly & out["soil"].notna() & out["rate_6h"].notna() & (out["cycle"] > 0)
    return out[keep].reset_index(drop=True)


def features(data: pd.DataFrame) -> pd.DataFrame:
    return data[FEATURES].fillna(NEUTRAL)


def make_estimator(kind: str):
    if kind == "ridge":
        return make_pipeline(SimpleImputer(), StandardScaler(), Ridge(alpha=1.0))
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                                         l2_regularization=1.0, random_state=0)


@dataclass
class ForecastModel:
    """Модели изменения влажности на горизонтах HORIZONS + модель времени до порога.

    Время до порога предсказывается напрямую (в log-шкале, признак gap = влажность − порог):
    циклы длятся от суток до недель, и линейное продолжение скорости за 24 ч ошибается сильно.
    """
    kind: str = "gbr"
    models: dict = field(default_factory=dict)
    ttt_model: object = None
    # Медианная скорость высыхания обучающих данных, п. п./ч (бейзлайн и запасной вариант)
    mean_rate: float = 0.5
    # Квантили ошибки по горизонтам (для доверительного интервала): {h: [q10, q90]}
    residuals: dict = field(default_factory=dict)

    def fit(self, data: pd.DataFrame) -> "ForecastModel":
        for h in HORIZONS:
            d = data.dropna(subset=[f"d{h}"])
            self.models[h] = make_estimator(self.kind).fit(features(d), d[f"d{h}"])
        t = ttt_rows(data)
        self.ttt_model = make_estimator(self.kind).fit(ttt_features(t), np.log1p(t["ttt_true"]))
        drops = -data["d24"].dropna() / 24
        drops = drops[drops > 0]
        self.mean_rate = float(drops.median()) if len(drops) else self.mean_rate
        return self

    def predict(self, data: pd.DataFrame, scale: float = 1.0) -> dict[int, np.ndarray]:
        """Изменение влажности (п. п.) на каждом горизонте.

        scale — адаптация: во сколько раз дольше прогноза сохнет своё растение (скорость / scale).
        """
        x = features(data)
        return {h: m.predict(x) / scale for h, m in self.models.items()}

    def ttt(self, data: pd.DataFrame, scale: float = 1.0) -> np.ndarray:
        """Часы до порога (порог — колонка threshold)."""
        hours = np.expm1(self.ttt_model.predict(ttt_features(data))) * scale
        hours = np.where(data["soil"] <= data["threshold"], 0.0, hours)
        return np.clip(hours, 0, TTT_CAP_H)

    def calibrate_residuals(self, data: pd.DataFrame) -> None:
        pred = self.predict(data)
        for h in HORIZONS:
            err = (data[f"d{h}"] - pred[h]).dropna()
            if len(err):
                self.residuals[h] = [float(err.quantile(0.1)), float(err.quantile(0.9))]


def ttt_rows(data: pd.DataFrame) -> pd.DataFrame:
    """Примеры для времени до порога: порог достигнут в этом цикле, сейчас почва выше порога."""
    return data[data["ttt_true"].notna() & (data["soil"] > data["threshold"])]


def ttt_features(data: pd.DataFrame) -> pd.DataFrame:
    x = features(data)
    x["gap"] = data["soil"] - data["threshold"]
    return x


def fit_scale(model: ForecastModel, own: pd.DataFrame, prior_cycles: float = 1.0) -> float:
    """Адаптация по своим циклам: во сколько раз дольше прогноза сохнет своё растение.

    По каждому циклу — медиана log(факт / прогноз) времени до порога; среднее по циклам
    подтягивается к 1 (prior_cycles «виртуальных» циклов без поправки), чтобы один цикл
    не уводил прогноз в крайность.
    """
    rows = ttt_rows(own)
    if rows.empty:
        return 1.0
    ratio = pd.Series(np.log1p(rows["ttt_true"]) - np.log1p(model.ttt(rows)), index=rows.index)
    per_cycle = ratio.groupby(rows["cycle"]).median()
    n = len(per_cycle)
    return float(np.clip(np.exp(per_cycle.sum() / (n + prior_cycles)), *SCALE_LIMITS))


# ----- бейзлайны (6.1) -----

def persistence(data: pd.DataFrame) -> dict[int, np.ndarray]:
    """Влажность не меняется."""
    return {h: np.zeros(len(data)) for h in HORIZONS}


def linear(data: pd.DataFrame) -> dict[int, np.ndarray]:
    """Линейная экстраполяция скорости за последние 6 ч."""
    return {h: data["rate_6h"].to_numpy() * h for h in HORIZONS}


def linear_ttt(data: pd.DataFrame) -> np.ndarray:
    return time_to_threshold(data["soil"], data["rate_6h"], data["threshold"])


def mean_rate_ttt(data: pd.DataFrame, rate: float) -> np.ndarray:
    """Средняя скорость высыхания обучающих данных."""
    return time_to_threshold(data["soil"], np.full(len(data), -rate), data["threshold"])
