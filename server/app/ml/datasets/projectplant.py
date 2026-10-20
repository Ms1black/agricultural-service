"""GitHub jeffschwane/ProjectPlant: почасовые данные 4 датчиков Flower Care в комнатных растениях.

Файл — экспорт приложения (UTF-16, табуляция): блок на датчик, в строке-заголовке — растение
и даты (по 4 колонки L/S/T/E на день), далее 24 строки с часами 01:00…00:00. «--» — нет данных.
Последний экспорт содержит всю историю, поэтому качаем только его.
"""

from datetime import timedelta
from pathlib import Path

import httpx
import pandas as pd

from . import Source, frame

FILE = "2021-02-18-12-HHCC.csv"
RAW_URL = "https://raw.githubusercontent.com/jeffschwane/ProjectPlant/main/" + FILE


def download(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    r = httpx.get(RAW_URL, timeout=60, follow_redirects=True)
    r.raise_for_status()
    (folder / FILE).write_bytes(r.content)


def parse(text: str) -> list[pd.DataFrame]:
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        head = lines[i].split("\t")
        if len(head) > 4 and len(head[1]) == 10 and head[1][4] == "-":
            plant = head[0].split(" (")[0].strip()
            dates = [(k, head[k]) for k in range(1, len(head)) if head[k]]
            rows = []
            j = i + 2                          # пропускаем строку L/S/T/E
            while j < len(lines) and len(lines[j]) >= 5 and lines[j][2] == ":":
                cells = lines[j].split("\t")
                hour = int(cells[0][:2]) or 24  # 00:00 — конец суток
                for k, day in dates:
                    if k + 2 < len(cells) and cells[k + 1] != "--":
                        ts = pd.Timestamp(day) + timedelta(hours=hour)
                        rows.append((ts, cells[k + 1], cells[k + 2]))
                j += 1
            if rows:
                df = pd.DataFrame(rows, columns=["ts", "soil", "temp"])
                key = plant.lower().replace(" ", "_")
                # Время — местное (Нью-Йорк), часовой пояс в файле не указан
                out.append(frame(df["ts"], "projectplant", f"projectplant/{key}",
                                 soil_pct=df["soil"], temp=df["temp"]))
            i = j
        else:
            i += 1
    return out


def load(folder: Path) -> list[pd.DataFrame]:
    return parse((folder / FILE).read_bytes().decode("utf-16"))


SOURCE = Source(
    key="projectplant",
    title="GitHub jeffschwane/ProjectPlant",
    url="https://github.com/jeffschwane/ProjectPlant",
    license="лицензия в репозитории не указана — только для исследования, данные не публикуем",
    fields="влажность почвы %, температура, освещённость, EC — 4 датчика Flower Care, почасово",
    note="поливы не размечены; есть пропуски (память датчиков перезаписывалась)",
    download=download,
    load=load,
)
