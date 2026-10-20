"""Подготовка рядов (M0): регулярная сетка, нормализация, поливы, признаки, качество.

Нормализация — чтобы модель не зависела от датчика: влажность в % по мин/макс каждого ряда
(1-й и 99-й перцентили; сырое АЦП обратно пропорционально влажности), скорость высыхания в %/ч,
время с последнего полива в часах.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Полив, найденный по данным: рост нормализованной влажности за окно (п. п.)
WATER_RISE_PCT = 10.0
WATER_RISE_WINDOW_H = 3.0
WATER_MIN_GAP_H = 12.0
# Полный цикл «полив → высыхание»: не короче и с падением не меньше
CYCLE_MIN_H = 12.0
CYCLE_MIN_DROP = 5.0
# Пропуск длиннее — разрыв ряда (не интерполируем)
MAX_FILL_STEPS = 2
HOURS_SINCE_CAP = 24 * 14.0


def step_of(df: pd.DataFrame) -> pd.Timedelta:
    """Шаг сетки по медианному интервалу: 15 мин, 30 мин или 1 ч."""
    dt = df["ts"].diff().median()
    for step in ("15min", "30min", "1h"):
        if dt <= pd.Timedelta(step) * 1.5:
            return pd.Timedelta(step)
    return pd.Timedelta("1h")


def to_grid(df: pd.DataFrame, step: pd.Timedelta | None = None) -> pd.DataFrame:
    """Ряд → регулярная сетка; короткие пропуски интерполируются, длинные остаются NaN."""
    step = step or step_of(df)
    g = df.set_index("ts")
    num = g[["temp", "air_hum", "soil_raw", "soil_pct"]].resample(step).mean()
    num = num.interpolate(limit=MAX_FILL_STEPS, limit_area="inside")
    num["watering_event"] = g["watering_event"].resample(step).max().fillna(0).astype(bool)
    out = num.reset_index()
    out["source"], out["series"] = df["source"].iloc[0], df["series"].iloc[0]
    out["watering_inferred"] = bool(df["watering_inferred"].iloc[0])
    return out


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """soil → 0…100 % по перцентилям ряда. Сырое АЦП: больше = суше."""
    df = df.copy()
    if df["soil_pct"].notna().sum() >= 10:
        x, inverse = df["soil_pct"], False
    else:
        x, inverse = df["soil_raw"], True
    lo, hi = np.nanpercentile(x, 1), np.nanpercentile(x, 99)
    span = max(hi - lo, 1e-9)
    pct = (hi - x) / span * 100 if inverse else (x - lo) / span * 100
    df["soil"] = pct.clip(0, 100)
    return df


def infer_watering(df: pd.DataFrame, rise: float = WATER_RISE_PCT,
                   window_h: float = WATER_RISE_WINDOW_H) -> pd.Series:
    """Полив = рост влажности ≥ rise п. п. за window_h.

    События ближе WATER_MIN_GAP_H склеиваются в одно.
    """
    step_h = df["ts"].diff().median() / pd.Timedelta("1h")
    k = max(1, round(window_h / step_h))
    gain = df["soil"].shift(-k) - df["soil"]
    candidates = df.index[gain >= rise]
    events = pd.Series(False, index=df.index)
    last = None
    for i in candidates:
        if last is None or (df.at[i, "ts"] - df.at[last, "ts"]) >= pd.Timedelta(
                hours=WATER_MIN_GAP_H):
            events.at[i] = True
            last = i
    return events


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Признаки: влажность, скорость за 3 и 6 ч (%/ч), часы с полива, температура, час суток."""
    df = df.copy()
    step_h = df["ts"].diff().median() / pd.Timedelta("1h")
    for h in (3, 6):
        k = max(1, round(h / step_h))
        df[f"rate_{h}h"] = (df["soil"] - df["soil"].shift(k)) / (k * step_h)
    water_ts = df["ts"].where(df["watering_event"]).ffill()
    df["hours_since_water"] = ((df["ts"] - water_ts) / pd.Timedelta("1h")).clip(
        upper=HOURS_SINCE_CAP).fillna(HOURS_SINCE_CAP)
    hour = df["ts"].dt.hour + df["ts"].dt.minute / 60
    df["hour_sin"], df["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    df["cycle"] = df["watering_event"].cumsum()
    return df


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """Сырой ряд в единой схеме → сетка, нормализация, поливы (размеченные/найденные), признаки."""
    df = normalize(to_grid(raw))
    if bool(raw["watering_inferred"].iloc[0]) or not df["watering_event"].any():
        df["watering_event"] = infer_watering(df)
        df["watering_inferred"] = True
    return add_features(df)


def cycles(df: pd.DataFrame) -> list[tuple[int, int]]:
    """Полные циклы «полив → высыхание»: (индекс полива, индекс следующего полива)."""
    idx = list(df.index[df["watering_event"]])
    out = []
    for a, b in zip(idx, idx[1:], strict=False):
        seg = df.loc[a:b - 1, "soil"]
        hours = (df.at[b, "ts"] - df.at[a, "ts"]) / pd.Timedelta("1h")
        if hours >= CYCLE_MIN_H and seg.notna().mean() > 0.8 and (
                seg.max() - seg.iloc[-max(1, len(seg) // 10):].mean()) >= CYCLE_MIN_DROP:
            out.append((a, b))
    return out


@dataclass
class Quality:
    series: str
    rows: int
    days: float
    step: str
    missing_share: float
    gaps: int                # разрывов длиннее суток
    cycles: int
    autocorr: float          # автокорреляция влажности с лагом 1 шаг
    usable: bool
    reason: str


def quality(raw: pd.DataFrame, df: pd.DataFrame) -> Quality:
    days = (df["ts"].iloc[-1] - df["ts"].iloc[0]) / pd.Timedelta("1D") if len(df) else 0.0
    missing = float(df["soil"].isna().mean()) if len(df) else 1.0
    dts = raw["ts"].diff()
    gaps = int((dts > pd.Timedelta("1D")).sum())
    ac = float(df["soil"].autocorr(1)) if df["soil"].notna().sum() > 10 else 0.0
    n_cycles = len(cycles(df))
    reasons = []
    if days < 7:
        reasons.append("меньше недели данных")
    if missing > 0.5:
        reasons.append("больше половины пропусков")
    # У настоящего ряда влажности соседние точки почти совпадают; низкая автокорреляция —
    # признак шума или синтетической таблицы без временной непрерывности
    if ac < 0.9:
        reasons.append(f"нет временной непрерывности (автокорреляция {ac:.2f})")
    if n_cycles < 2:
        reasons.append("меньше 2 полных циклов")
    return Quality(series=str(raw["series"].iloc[0]), rows=len(raw), days=round(days, 1),
                   step=str(step_of(raw)), missing_share=round(missing, 3), gaps=gaps,
                   cycles=n_cycles, autocorr=round(ac, 3), usable=not reasons,
                   reason="; ".join(reasons) or "ок")
