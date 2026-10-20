"""Обучение прогноза высыхания (M1): оценка против бейзлайнов → регистрация → отчёт."""

import json
import time
from pathlib import Path

import pandas as pd

from ..db import Database
from . import evaluate as ev
from . import model as fm
from . import policy_sim, registry
from .datasets import data_dir, sources
from .prepare import prepare, quality


def open_data(root: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Примеры из пригодных рядов открытых датасетов + описание, на чём обучаемся.

    Подготовленные ряды целиком — в trained_on["_series"] (для симуляции политик, не сохраняются).
    """
    root = root or data_dir()
    parts, used, frames = [], [], []
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
            frames.append(df)
            used.append({"source": key, "series": q.series, "cycles": q.cycles,
                         "from": f"{raw['ts'].iloc[0]:%Y-%m-%d}",
                         "to": f"{raw['ts'].iloc[-1]:%Y-%m-%d}"})
    if not parts:
        raise RuntimeError(f"Нет данных в {root}. Сначала: python -m app.ml.datasets download")
    data = pd.concat(parts, ignore_index=True)
    return data, {"sources": sorted({u["source"] for u in used}), "series": used,
                  "own_cycles": 0, "_series": frames}


OWN_MIN_CYCLES = 3
RECENT_DAYS = 7


def with_own_data(db: Database, data: pd.DataFrame, trained_on: dict,
                  now: float | None = None) -> tuple[pd.DataFrame, dict]:
    """Добавить данные своих устройств, если у устройства ≥ 3 полных циклов (M2)."""
    from .. import store
    from .forecast import device_frame
    now = now or time.time()
    parts = [data]
    for device in store.devices(db):
        df = device_frame(db, device, now, days=365)
        if df is None:
            continue
        cal = store.get_calibration(db, device) or {}
        own = fm.samples(df, cal.get("target_low") or fm.DEFAULT_THRESHOLD)
        cycles = int(fm.ttt_rows(own)["cycle"].nunique())
        if cycles < OWN_MIN_CYCLES:
            continue
        own["source"], own["series"] = "device", f"device/{device}"
        parts.append(own)
        trained_on = {**trained_on, "own_cycles": trained_on.get("own_cycles", 0) + cycles,
                      "sources": sorted({*trained_on["sources"], "device"}),
                      "series": [*trained_on["series"], {
                          "source": "device", "series": f"device/{device}", "cycles": cycles,
                          "from": f"{df['ts'].iloc[0]:%Y-%m-%d}",
                          "to": f"{df['ts'].iloc[-1]:%Y-%m-%d}"}]}
    return pd.concat(parts, ignore_index=True), trained_on


def _pick(r: dict) -> dict:
    return {"ttt_mae_h": r.get("model_ttt_mae_h"), "mae_6h": r.get("model_mae_6h")}


def recent_comparison(db: Database, data: pd.DataFrame, kind: str) -> tuple[dict, dict] | None:
    """6.6: новая модель (без последних 7 дней своих данных) и текущая рабочая — на них."""
    own = data[data["source"] == "device"]
    champ = registry.champion(db, registry.TASK_DRYING)
    if own.empty or champ is None:
        return None
    cut = own["ts"].max() - pd.Timedelta(days=RECENT_DAYS)
    recent = own[own["ts"] > cut]
    if recent.empty:
        return None
    challenger = fm.ForecastModel(kind).fit(data.drop(recent.index))
    return (_pick(ev.score(challenger, recent)),
            _pick(ev.score(registry.load(champ), recent)))


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

    frames = trained_on.pop("_series", None)
    policy = policy_sim.compare(frames, final) if frames else None

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
        "policy_sim": policy,
    }
    recent = recent_comparison(db, data, kind)
    if recent:
        metrics["recent_7d"] = {"challenger": recent[0], "champion": recent[1]}
    entry = registry.save(db, models_dir, registry.TASK_DRYING, final, metrics, trained_on,
                          recent=recent)
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
    if m.get("policy_sim"):
        t, ml = m["policy_sim"]["total"]["threshold"], m["policy_sim"]["total"]["ml"]
        lines += [
            "## Политика полива: threshold против ml (6.4, офлайн-симуляция)", "",
            "Высыхание — как в реальных рядах, отклик на полив — "
            f"{policy_sim.K_DOSE:g} %/с. threshold — как прошивка (ниже 30 % — 3 с), ml — по "
            "прогнозу и дозе до 65 % с лимитами раздела 5. "
            f"Всего {t['days']:g} суток.", "",
            "| Метрика | threshold | ml |", "|---|---|---|",
            f"| Время в диапазоне 30–70 % | {t['in_range_share']:.1%} "
            f"| {ml['in_range_share']:.1%} |",
            f"| Часы пересыхания (< 30 %) | {t['hours_dry']:g} | {ml['hours_dry']:g} |",
            f"| Часы перелива (> 70 %) | {t['hours_wet']:g} | {ml['hours_wet']:g} |",
            f"| Средняя влажность, % | {t['mean_soil']:g} | {ml['mean_soil']:g} |",
            f"| Число поливов | {t['waterings']} | {ml['waterings']} |",
            f"| Работа насоса, с (≈ расход воды) | {t['pump_seconds']:g} "
            f"| {ml['pump_seconds']:g} |",
            "", "| Ряд | В диапазоне (thr / ml) | Пересыхание, ч | Поливов |", "|---|---|---|---|"]
        for name, r in m["policy_sim"]["per_series"].items():
            a, b = r["threshold"], r["ml"]
            lines.append(f"| {name} | {a['in_range_share']:.1%} / {b['in_range_share']:.1%} "
                         f"| {a['hours_dry']:g} / {b['hours_dry']:g} "
                         f"| {a['waterings']} / {b['waterings']} |")
        lines += ["", "threshold держит почву у самого края пересыхания (средняя влажность "
                  "≈ порог) и включает насос в разы чаще; ml держит середину диапазона. Прогноз в "
                  "симуляции не адаптирован под ряд — на быстро сохнущем горшке это даёт часы "
                  "пересыхания; адаптация (6.3) и режим ml только при уверенной калибровке (C1) "
                  "нужны именно поэтому.", ""]
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
