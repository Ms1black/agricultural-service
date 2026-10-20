"""Обучение прогноза высыхания (M1): оценка против бейзлайнов → регистрация → отчёт."""

import json
from pathlib import Path

import pandas as pd

from ..db import Database
from . import evaluate as ev
from . import model as fm
from . import registry
from .datasets import data_dir, sources
from .prepare import prepare, quality


def open_data(root: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Примеры из пригодных рядов открытых датасетов + описание, на чём обучаемся."""
    root = root or data_dir()
    parts, used = [], []
    for key, s in sources().items():
        folder = root / key
        if s.auxiliary or not folder.is_dir():
            continue
        for raw in s.load(folder):
            df = prepare(raw)
            q = quality(raw, df)
            if not q.usable:
                continue
            parts.append(fm.samples(df))
            used.append({"source": key, "series": q.series, "cycles": q.cycles,
                         "from": f"{raw['ts'].iloc[0]:%Y-%m-%d}",
                         "to": f"{raw['ts'].iloc[-1]:%Y-%m-%d}"})
    if not parts:
        raise RuntimeError(f"Нет данных в {root}. Сначала: python -m app.ml.datasets download")
    data = pd.concat(parts, ignore_index=True)
    return data, {"sources": sorted({u["source"] for u in used}), "series": used,
                  "own_cycles": 0}


def train(db: Database, models_dir: str, data: pd.DataFrame, trained_on: dict,
          report_path: Path | None = None) -> dict:
    results = {kind: ev.time_split(data, kind) for kind in ("gbr", "ridge")}
    kind = min(results, key=lambda k: results[k]["model_ttt_mae_h"] or float("inf"))
    best = results[kind]
    loso = ev.leave_one_source_out(data, kind)
    curve = ev.adaptation_curve(data, kind)

    # Интервал прогноза — по ошибкам на проверочной части; финальная модель — на всех данных
    train_part, test_part = ev.split_by_time(data)
    probe = fm.ForecastModel(kind).fit(train_part)
    probe.calibrate_residuals(test_part)
    final = fm.ForecastModel(kind).fit(data)
    final.residuals = probe.residuals

    metrics = {
        "kind": kind,
        "mae_6h": best["model_mae_6h"], "mae_12h": best["model_mae_12h"],
        "mae_24h": best["model_mae_24h"],
        "ttt_mae_h": best["model_ttt_mae_h"], "ttt_median_h": best["model_ttt_median_h"],
        "ttt_within_3h": best["model_ttt_within_3h"],
        "baselines": {k: v for k, v in best.items()
                      if k.startswith(("persistence_", "linear_", "mean_rate_"))},
        "gain_ttt_vs_linear": best["gain_ttt_vs_linear"],
        "beats_baseline": bool(
            best["model_ttt_mae_h"] is not None
            and best["model_ttt_mae_h"] < min(best["linear_ttt_mae_h"],
                                              best["mean_rate_ttt_mae_h"])
            and best["model_mae_6h"] <= best["linear_mae_6h"]),
        "time_split": results, "leave_one_source_out": loso, "adaptation": curve,
    }
    entry = registry.save(db, models_dir, registry.TASK_DRYING, final, metrics, trained_on)
    if report_path:
        write_report(report_path, entry, metrics, trained_on)
    return {**entry, "metrics": metrics}


def write_report(path: Path, entry: dict, m: dict, trained_on: dict) -> None:
    b = m["baselines"]
    lines = [
        "# Прогноз высыхания: отчёт обучения (M1)", "",
        f"Версия `{entry['version']}`, модель `{m['kind']}`, "
        f"{'**рабочая**' if entry['is_champion'] else 'не выкачена'}.", "",
        "Данные: открытые датасеты (см. [datasets.md](datasets.md)), "
        f"своих циклов: {trained_on.get('own_cycles', 0)}.", "",
        "## Разбиение по времени (70 % / 30 % каждого ряда)", "",
        "| Метрика | Модель | Persistence | Линейная (6 ч) | Средняя скорость |",
        "|---|---|---|---|---|",
    ]
    for h in fm.HORIZONS:
        lines.append(f"| MAE влажности, {h} ч, п. п. | {m[f'mae_{h}h']} "
                     f"| {b[f'persistence_mae_{h}h']} | {b[f'linear_mae_{h}h']} | — |")
    lines += [
        f"| **Время до порога: MAE, ч** | **{m['ttt_mae_h']}** | — | {b['linear_ttt_mae_h']} "
        f"| {b['mean_rate_ttt_mae_h']} |",
        f"| Время до порога: медиана, ч | {m['ttt_median_h']} | — | {b['linear_ttt_median_h']} "
        f"| {b['mean_rate_ttt_median_h']} |",
        f"| Доля прогнозов ±3 ч | {m['ttt_within_3h']:.1%} | — | {b['linear_ttt_within_3h']:.1%} "
        f"| {b['mean_rate_ttt_within_3h']:.1%} |", "",
        f"Улучшение времени до порога против линейного бейзлайна: "
        f"**{m['gain_ttt_vs_linear']:.0%}**.", "",
        "## Перенос на другой источник (leave-one-source-out)", "",
        "| Проверка на | MAE 6 ч (модель / persistence) | Время до порога, MAE ч "
        "(модель / линейная / средняя скорость) |", "|---|---|---|",
    ]
    for src, r in m["leave_one_source_out"].items():
        lines.append(f"| {src} | {r['model_mae_6h']} / {r['persistence_mae_6h']} "
                     f"| {r['model_ttt_mae_h']} / {r['linear_ttt_mae_h']} "
                     f"/ {r['mean_rate_ttt_mae_h']} |")
    lines += ["", "Перенос на незнакомый горшок без адаптации — слабое место: скорость "
              "высыхания разных горшков различается в разы. Поэтому ниже — адаптация.", "",
              "## Адаптация под своё растение (6.3)", "",
              "Модель обучена без этого ряда; первые циклы ряда — «свои», проверка — на остальных.",
              "", "| Ряд | 0 | 1 | 2 | 3 | 4 | 5 циклов |", "|---|---|---|---|---|---|---|"]
    for series, pts in m["adaptation"].items():
        name = "**среднее**" if series == "_mean" else series
        lines.append(f"| {name} | " + " | ".join(str(p["ttt_mae_h"]) for p in pts) + " |")
    lines += ["", "Значения — MAE времени до порога, ч.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def default_report() -> Path | None:
    docs = Path(__file__).resolve().parents[3] / "docs"
    return docs / "ml_report.md" if docs.is_dir() else None


def summary(result: dict) -> str:
    m = result["metrics"]
    return json.dumps({"version": result["version"], "champion": result["is_champion"],
                       "kind": m["kind"], "ttt_mae_h": m["ttt_mae_h"],
                       "linear_ttt_mae_h": m["baselines"]["linear_ttt_mae_h"],
                       "mae_6h": m["mae_6h"], "gain_ttt_vs_linear": m["gain_ttt_vs_linear"]},
                      ensure_ascii=False)
