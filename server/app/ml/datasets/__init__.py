"""Открытые датасеты (M0): загрузка и приведение к единой схеме.

Единая схема (совместима с таблицей measurements): один DataFrame на ряд (датчик) с колонками
    ts (UTC, datetime), source, series, temp (°C), air_hum (%), soil_raw, soil_pct, watering_event
и флагом watering_inferred (полив не размечен в источнике, найден по росту влажности).
soil_pct здесь — значение источника (% или сырое АЦП → %); нормализация — в prepare.py.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

COLUMNS = ["ts", "source", "series", "temp", "air_hum", "soil_raw", "soil_pct",
           "watering_event", "watering_inferred"]


@dataclass(frozen=True)
class Source:
    key: str
    title: str
    url: str
    license: str
    fields: str
    note: str
    download: Callable[[Path], None]
    load: Callable[[Path], list[pd.DataFrame]]
    # Только для проверки обобщения (не для обучения основной модели)
    auxiliary: bool = False


def data_dir() -> Path:
    """data/external в корне репозитория (локально) или /data/external (Docker)."""
    if os.environ.get("PLANT_EXTERNAL_DIR"):
        return Path(os.environ["PLANT_EXTERNAL_DIR"])
    repo = Path(__file__).resolve().parents[4]
    if (repo / "server").is_dir():
        return repo / "data" / "external"
    return Path("/data/external")


def frame(ts, source: str, series: str, *, temp=None, air_hum=None, soil_raw=None,
          soil_pct=None, watering=None) -> pd.DataFrame:
    """Ряд в единой схеме; отсутствующие поля — NaN. watering=None → поливы не размечены."""
    ts = pd.to_datetime(pd.Series(ts).reset_index(drop=True), utc=True)
    n = len(ts)

    def col(v):
        if v is None:
            return pd.Series([float("nan")] * n)
        return pd.to_numeric(pd.Series(v).reset_index(drop=True), errors="coerce")

    df = pd.DataFrame({
        "ts": ts, "source": source, "series": series, "temp": col(temp),
        "air_hum": col(air_hum), "soil_raw": col(soil_raw), "soil_pct": col(soil_pct),
        "watering_event": (pd.Series(watering).reset_index(drop=True).fillna(False).astype(bool)
                           if watering is not None else False),
        "watering_inferred": watering is None,
    })
    return df.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def sources() -> dict[str, Source]:
    from . import mendeley, projectplant, thingspeak
    items = [thingspeak.AGLAONEMA, thingspeak.SOIL_MONITOR, projectplant.SOURCE, mendeley.SOURCE]
    return {s.key: s for s in items}


def load_all(root: Path | None = None, include_auxiliary: bool = True) -> list[pd.DataFrame]:
    root = root or data_dir()
    out = []
    for s in sources().values():
        if s.auxiliary and not include_auxiliary:
            continue
        folder = root / s.key
        if folder.is_dir() and any(folder.iterdir()):
            out.extend(s.load(folder))
    return out
