"""Реестр моделей (M1/M2): таблица models + файлы <models_dir>/<task>/<версия>/model.joblib."""

import json
import time
from pathlib import Path

import joblib

from ..db import Database
from .metrics import beats

TASK_DRYING = "drying"


def save(db: Database, models_dir: str, task: str, model, metrics: dict, trained_on: dict,
         version: str | None = None) -> dict:
    """Сохранить модель; рабочей (champion) она становится, только если лучше бейзлайна
    и лучше текущей рабочей (правило 6.6). Возвращает запись реестра."""
    version = version or time.strftime("%Y%m%d-%H%M%S")
    folder = Path(models_dir) / task / version
    folder.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, folder / "model.joblib")
    (folder / "meta.json").write_text(json.dumps(
        {"task": task, "version": version, "metrics": metrics, "trained_on": trained_on},
        ensure_ascii=False, indent=2), encoding="utf-8")

    current = champion(db, task)
    better_than_baseline = bool(metrics.get("beats_baseline"))
    promote = better_than_baseline and (current is None or beats(metrics, current["metrics"]))
    with db.conn() as c:
        if promote:
            c.execute("UPDATE models SET is_champion = 0 WHERE task = ?", (task,))
        c.execute(
            "INSERT INTO models (task, version, created, metrics_json, is_champion, "
            "trained_on_json, path) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task, version, time.time(), json.dumps(metrics, ensure_ascii=False), int(promote),
             json.dumps(trained_on, ensure_ascii=False), str(folder)),
        )
    return {"version": version, "is_champion": promote, "path": str(folder)}


def _row(r) -> dict:
    return {"id": r["id"], "task": r["task"], "version": r["version"], "created": r["created"],
            "metrics": json.loads(r["metrics_json"]), "is_champion": bool(r["is_champion"]),
            "trained_on": json.loads(r["trained_on_json"]), "path": r["path"]}


def champion(db: Database, task: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM models WHERE task = ? AND is_champion = 1 "
                      "ORDER BY id DESC LIMIT 1", (task,)).fetchone()
    return _row(r) if r else None


def history(db: Database, task: str, limit: int = 20) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM models WHERE task = ? ORDER BY id DESC LIMIT ?",
                         (task, limit)).fetchall()
    return [_row(r) for r in rows]


_cache: dict[str, object] = {}


def load(entry: dict):
    """Модель из файла (с кэшем по пути)."""
    path = entry["path"]
    if path not in _cache:
        _cache[path] = joblib.load(Path(path) / "model.joblib")
    return _cache[path]
