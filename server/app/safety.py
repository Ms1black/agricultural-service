"""Слой безопасности полива (раздел 5 PLAN.md). Через него идут поливы по решению сервера и голосу.

Ни ML, ни LLM не управляют реле напрямую: они только просят полив, а решает этот модуль.
"""

import time
from dataclasses import dataclass

from . import store
from .db import Database

MAX_WATER_SECONDS = store.MAX_WATER_SECONDS     # 10 с
MIN_INTERVAL_S = 30 * 60                         # между поливами по решению сервера / голосу
MAX_PER_DAY = 6
MAX_SOIL_PCT = 70                                # влажнее — не поливать
FRESH_DATA_S = 120                               # нет свежих данных — решений не принимать
# D1: демо-режим — только явным флагом DEMO_MODE и с пометкой DEMO на экране и в вебе.
# Уменьшает лишь интервал между поливами сервера/голоса; 10 с, 70 %, 6 в сутки — как всегда.
DEMO_MIN_INTERVAL_S = 2 * 60
demo_mode = False


def min_interval_s() -> float:
    return DEMO_MIN_INTERVAL_S if demo_mode else MIN_INTERVAL_S


# Поливы, которые считаются «по решению сервера / голосу»
SERVER_REASONS = ("voice", "ml", "web", "calib")


@dataclass
class Verdict:
    ok: bool
    seconds: int
    reason: str


def check(db: Database, device: str, seconds: float, now: float | None = None,
          max_per_day: int = MAX_PER_DAY) -> Verdict:
    now = now or time.time()
    interval = min_interval_s()
    seconds = int(max(1, min(MAX_WATER_SECONDS, round(seconds))))
    m = store.latest_measurement(db, device)
    if not m or now - m["ts"] > FRESH_DATA_S:
        return Verdict(False, 0, "нет свежих данных с устройства")
    if m["soil_ok"] and m["soil_pct"] is not None and m["soil_pct"] > MAX_SOIL_PCT:
        return Verdict(False, 0, f"почва достаточно влажная ({m['soil_pct']} %)")
    if not m["soil_ok"] and not m["soil_dry_d0"]:
        return Verdict(False, 0, "датчик показывает, что почва влажная")
    from .calibration import anomaly_active
    anomaly = anomaly_active(db, device)
    if anomaly:
        return Verdict(False, 0, "аномалия полива не подтверждена: " + anomaly["text"])
    if store.pending_command(db, device, 120):
        return Verdict(False, 0, "полив уже ждёт отправки на устройство")
    with db.conn() as c:
        rows = c.execute(
            "SELECT ts, note FROM events WHERE device = ? AND type = 'water' AND ts >= ? "
            "ORDER BY ts DESC", (device, now - 86400)).fetchall()
    if len(rows) >= max_per_day:
        return Verdict(False, 0, f"сегодня уже {len(rows)} поливов — это максимум")
    last_server = next((r["ts"] for r in rows if r["note"] in SERVER_REASONS), None)
    if last_server and now - last_server < interval:
        wait = int((interval - (now - last_server)) // 60) + 1
        return Verdict(False, 0, f"недавно уже поливали, следующий полив — через {wait} мин")
    return Verdict(True, seconds, "можно поливать")


def request_water(db: Database, device: str, seconds: float, note: str,
                  now: float | None = None) -> Verdict:
    """Проверить и, если можно, поставить полив в очередь устройства."""
    v = check(db, device, seconds, now)
    if v.ok:
        store.queue_water(db, device, v.seconds, note)
    return v
