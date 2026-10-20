"""Mendeley Data m6j79zjyd7 «soil moisture dataset»: почасовые данные поля, 2017–2018.

Вспомогательный источник: «полив» здесь — осадки, используем только для проверки обобщения.
"""

from pathlib import Path

import httpx
import pandas as pd

from . import Source, frame

FILE = "soil_data_incl_rain_v3.csv"
URL = ("https://data.mendeley.com/public-files/datasets/m6j79zjyd7/files/"
       "ffa66cce-fc80-47ce-86ec-f376f937359d/file_downloaded")


def download(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    r = httpx.get(URL, timeout=60, follow_redirects=True)
    r.raise_for_status()
    (folder / FILE).write_bytes(r.content)


def load(folder: Path) -> list[pd.DataFrame]:
    df = pd.read_csv(folder / FILE)
    return [frame(df["Time"], "mendeley", "mendeley_field", soil_pct=df["Soil_Moisture"],
                  temp=df["Atmospheric_Temp"], air_hum=df["Humidity"])]


SOURCE = Source(
    key="mendeley",
    title="Mendeley Data m6j79zjyd7 «soil moisture dataset»",
    url="https://data.mendeley.com/datasets/m6j79zjyd7/1",
    license="CC BY 4.0 (автор — Kayode Adetunji)",
    fields="влажность почвы, температура и влажность воздуха, температура почвы, точка росы",
    note="поле, не горшок; «полив» = осадки; только для проверки обобщения",
    download=download,
    load=load,
    auxiliary=True,
)
