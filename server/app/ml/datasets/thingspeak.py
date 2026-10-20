"""ThingSpeak: каналы 3062844 «Indoor Plant Aglaonema» и 983239 «Soil Monitor II».

API отдаёт не больше 8000 записей за запрос — качаем окнами по времени и склеиваем в feed.csv.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from . import Source, frame

API = "https://api.thingspeak.com/channels/{id}"
WINDOW = timedelta(days=30)


def download_channel(channel: int, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60) as http:
        info = http.get(API.format(id=channel) + "/feeds.json", params={"results": 1}).json()
        start = datetime.fromisoformat(info["channel"]["created_at"].replace("Z", "+00:00"))
        last = info["feeds"][-1]["created_at"] if info["feeds"] else info["channel"]["created_at"]
        end = datetime.fromisoformat(last.replace("Z", "+00:00")) + timedelta(seconds=1)
        parts = []
        t = start
        while t < end:
            r = http.get(API.format(id=channel) + "/feeds.csv", params={
                "start": t.strftime("%Y-%m-%d %H:%M:%S"),
                "end": min(t + WINDOW, end).strftime("%Y-%m-%d %H:%M:%S"), "timezone": "UTC"})
            r.raise_for_status()
            lines = r.text.strip().splitlines()
            parts.append(lines if not parts else lines[1:])
            t += WINDOW
    rows = [line for part in parts for line in part]
    (folder / "feed.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (folder / "downloaded.txt").write_text(datetime.now(UTC).isoformat(), encoding="utf-8")


def read_feed(folder: Path) -> pd.DataFrame:
    df = pd.read_csv(folder / "feed.csv")
    df = df.drop_duplicates("entry_id")
    df["created_at"] = pd.to_datetime(df["created_at"].str.replace(" UTC", ""), utc=True)
    return df


# ----- 3062844: комнатная аглаонема с насосом -----

def load_aglaonema(folder: Path) -> list[pd.DataFrame]:
    df = read_feed(folder)
    pump = pd.to_numeric(df["field6"], errors="coerce")
    return [frame(df["created_at"], "thingspeak_aglaonema", "aglaonema",
                  soil_pct=df["field1"], air_hum=df["field2"], temp=df["field3"],
                  soil_raw=df["field4"], watering=(pump == 1) & (pump.shift(1) != 1))]


AGLAONEMA = Source(
    key="thingspeak_aglaonema",
    title="ThingSpeak 3062844 «Indoor Plant Aglaonema»",
    url="https://thingspeak.mathworks.com/channels/3062844",
    license="публичный канал ThingSpeak, лицензия не указана — только для исследования",
    fields="влажность почвы %, влажность и температура воздуха, raw датчика, "
           "команда и статус насоса",
    note="поливы размечены статусом насоса (field6)",
    download=lambda folder: download_channel(3062844, folder),
    load=load_aglaonema,
)


# ----- 983239: емкостный датчик + BME280, сырое 12-бит АЦП -----

def load_soil_monitor(folder: Path) -> list[pd.DataFrame]:
    df = read_feed(folder)
    temp_c = (pd.to_numeric(df["field2"], errors="coerce") - 32) * 5 / 9
    return [frame(df["created_at"], "thingspeak_soil_monitor", "soil_monitor",
                  soil_raw=df["field1"], temp=temp_c, air_hum=df["field4"])]


SOIL_MONITOR = Source(
    key="thingspeak_soil_monitor",
    title="ThingSpeak 983239 «Soil Monitor II»",
    url="https://thingspeak.mathworks.com/channels/983239",
    license="публичный канал ThingSpeak, лицензия не указана — только для исследования",
    fields="raw 12-бит АЦП емкостного датчика (обратно пропорционален влажности), "
           "температура (°F → °C) и влажность воздуха BME280",
    note="поливы не размечены — определяются по резкому росту влажности",
    download=lambda folder: download_channel(983239, folder),
    load=load_soil_monitor,
)
