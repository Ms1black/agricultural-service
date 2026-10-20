"""python -m app.ml.datasets download [источник…] | report [путь к .md]"""

import sys
from datetime import UTC, datetime
from pathlib import Path

from ..prepare import prepare, quality
from . import data_dir, sources

REPORT = Path(__file__).resolve().parents[4] / "docs" / "datasets.md"

HEADER = """# Открытые датасеты (M0)

Своего растения пока нет, поэтому модель на первом этапе обучается на открытых данных,
а на собственных циклах — адаптируется (M1). Сырые файлы — в `data/external/<источник>/`
(не в репозитории); повторная загрузка: `cd server && python -m app.ml.datasets download`,
отчёт: `python -m app.ml.datasets report`.

Единая схема (совместима с `measurements`): `ts, source, series, temp, air_hum, soil_pct,
watering_event` (+ `watering_inferred`, если полив найден по резкому росту влажности
≥ {rise:g} п. п. за {window:g} ч). Влажность нормализуется в % по 1-му/99-му перцентилю
каждого ряда.

Ряд используется, если: ≥ 7 дней, пропусков ≤ 50 %, автокорреляция влажности ≥ 0.9
(иначе — нет временной непрерывности, похоже на синтетическую таблицу), ≥ 2 полных циклов.
"""


def download(keys: list[str]) -> int:
    """Скачать источники; ошибка одного не останавливает остальные."""
    root = data_dir()
    failed = []
    for key, s in sources().items():
        if keys and key not in keys:
            continue
        print(f"{key}: загрузка…", flush=True)
        try:
            s.download(root / key)
        except Exception as e:  # сеть, 403 и т. п. — остальные источники всё равно качаем
            failed.append(key)
            role = " (вспомогательный, для обучения не нужен)" if s.auxiliary else ""
            print(f"{key}: не скачан{role}: {e}", flush=True)
    print(f"Готово: {root}" + (f"; не скачаны: {', '.join(failed)}" if failed else ""))
    # Ошибка — только если не скачался ни один основной источник
    main_ok = any(not s.auxiliary and k not in failed and (root / k).is_dir()
                  for k, s in sources().items())
    return 0 if main_ok else 1


def report(path: Path) -> None:
    from ..prepare import WATER_RISE_PCT, WATER_RISE_WINDOW_H
    root = data_dir()
    out = [HEADER.format(rise=WATER_RISE_PCT, window=WATER_RISE_WINDOW_H)]
    out.append("## Источники\n")
    out.append("| Источник | Лицензия | Поля | Примечание |\n|---|---|---|---|")
    for s in sources().values():
        role = " (вспомогательный)" if s.auxiliary else ""
        out.append(f"| [{s.title}]({s.url}){role} | {s.license} | {s.fields} | {s.note} |")
    out.append("\n## Качество рядов\n")
    out.append("| Ряд | Строк | Период | Дней | Шаг | Пропуски | Разрывы > 1 сут | Циклов "
               "| Автокорр. | Используем |\n|---|---|---|---|---|---|---|---|---|---|")
    total_cycles = 0
    for key, s in sources().items():
        folder = root / key
        if not folder.is_dir():
            out.append(f"| {key} | — | не скачан | | | | | | | |")
            continue
        for raw in s.load(folder):
            df = prepare(raw)
            q = quality(raw, df)
            period = f"{raw['ts'].iloc[0]:%Y-%m-%d} … {raw['ts'].iloc[-1]:%Y-%m-%d}"
            use = "да" if q.usable and not s.auxiliary else (
                "только проверка" if q.usable else f"нет: {q.reason}")
            if q.usable and not s.auxiliary:
                total_cycles += q.cycles
            step = f"{int(pd_minutes(q.step))} мин"
            out.append(f"| {q.series} | {q.rows} | {period} | {q.days:g} | {step} "
                       f"| {q.missing_share:.0%} | {q.gaps} | {q.cycles} | {q.autocorr:.3f} "
                       f"| {use} |")
    out.append(f"\nИтого для обучения: **{total_cycles} полных циклов** «полив → высыхание».\n")
    out.append(f"_Отчёт сгенерирован {datetime.now(UTC):%Y-%m-%d %H:%M} UTC._\n")
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"Отчёт: {path}")


def pd_minutes(step: str) -> float:
    import pandas as pd
    return pd.Timedelta(step) / pd.Timedelta("1min")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("download", "report"):
        print(__doc__)
        return 1
    if argv[0] == "download":
        return download(argv[1:])
    else:
        report(Path(argv[1]) if len(argv) > 1 else REPORT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
