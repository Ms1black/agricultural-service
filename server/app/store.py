"""Запросы к БД, общие для API устройства и веба."""

import time

from .db import Database

# Раздел 5 PLAN.md: максимум одного полива
MAX_WATER_SECONDS = 10


def save_measurement(db: Database, m: dict, ts: float | None = None) -> None:
    with db.conn() as c:
        c.execute(
            """INSERT INTO measurements
               (ts, device, temp, air_hum, soil_raw, soil_pct, soil_ok, soil_dry_d0)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts or time.time(), m["device"], m["temp"], m["air_hum"], m["soil_raw"],
             m["soil_pct"], int(m["soil_ok"]), int(m["soil_dry"])),
        )


def save_event(db: Database, device: str, type_: str, value: float | None,
               note: str | None) -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO events (ts, device, type, value, note) VALUES (?, ?, ?, ?, ?)",
            (time.time(), device, type_, value, note),
        )


def get_calibration(db: Database, device: str) -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM calibration WHERE device = ?", (device,)).fetchone()
    return dict(row) if row else None


def set_calibration(db: Database, device: str, dry_raw: int, wet_raw: int) -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO calibration (device, dry_raw, wet_raw, raw_dry, raw_wet, updated) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(device) DO UPDATE SET "
            "dry_raw = excluded.dry_raw, wet_raw = excluded.wet_raw, raw_dry = excluded.raw_dry, "
            "raw_wet = excluded.raw_wet, updated = excluded.updated",
            (device, dry_raw, wet_raw, dry_raw, wet_raw, time.time()),
        )


def queue_water(db: Database, device: str, seconds: int, note: str = "web") -> int:
    seconds = max(1, min(MAX_WATER_SECONDS, int(seconds)))
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO pending_commands (device, command, seconds, created, note) "
            "VALUES (?, 'water', ?, ?, ?)",
            (device, seconds, time.time(), note),
        )
    return seconds


def pending_command(db: Database, device: str, ttl_s: float) -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM pending_commands WHERE device = ?", (device,)).fetchone()
    if row and time.time() - row["created"] <= ttl_s:
        return dict(row)
    return None


def take_command(db: Database, device: str, ttl_s: float) -> dict | None:
    """Забрать команду (один раз). Просроченная команда удаляется без выполнения."""
    with db.conn() as c:
        row = c.execute("SELECT * FROM pending_commands WHERE device = ?", (device,)).fetchone()
        if not row:
            return None
        c.execute("DELETE FROM pending_commands WHERE device = ?", (device,))
        if time.time() - row["created"] <= ttl_s:
            c.execute("INSERT OR REPLACE INTO taken_commands (device, note, seconds, taken) "
                      "VALUES (?, ?, ?, ?)", (device, row["note"], row["seconds"], time.time()))
    if time.time() - row["created"] > ttl_s:
        return None
    return dict(row)


def command_reason(db: Database, device: str, within_s: float = 120) -> str | None:
    """Причина команды полива, недавно отданной устройству (voice, ml, web…)."""
    with db.conn() as c:
        row = c.execute("SELECT note, taken FROM taken_commands WHERE device = ?",
                        (device,)).fetchone()
    if row and time.time() - row["taken"] <= within_s:
        return row["note"]
    return None


def latest_measurement(db: Database, device: str) -> dict | None:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM measurements WHERE device = ? ORDER BY ts DESC, id DESC LIMIT 1",
            (device,),
        ).fetchone()
    return dict(row) if row else None


def measurements_since(db: Database, device: str, since_ts: float) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT ts, temp, air_hum, soil_raw, soil_pct, soil_ok, soil_dry_d0 "
            "FROM measurements WHERE device = ? AND ts >= ? ORDER BY ts",
            (device, since_ts),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_events(db: Database, device: str, limit: int = 20) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT ts, type, value, note FROM events WHERE device = ? "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (device, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def devices(db: Database) -> list[str]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT device FROM measurements GROUP BY device ORDER BY MAX(ts) DESC"
        ).fetchall()
    return [r["device"] for r in rows]
