"""SQLite: подключение и миграции схемы (версия — в таблице meta)."""

import os
import sqlite3
from contextlib import contextmanager

# Каждая миграция — список SQL-команд. Новые добавлять только в конец.
# CREATE TABLE IF NOT EXISTS — чтобы подхватить базу старого server.py без потерь.
MIGRATIONS: list[list[str]] = [
    # 1 — базовая схема (раздел 4.5 PLAN.md)
    [
        """CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            device TEXT NOT NULL,
            temp REAL,
            air_hum REAL,
            soil_raw INTEGER,
            soil_pct INTEGER,
            soil_ok INTEGER,
            soil_dry_d0 INTEGER
        )""",
        "CREATE INDEX IF NOT EXISTS ix_measurements_device_ts ON measurements(device, ts)",
        """CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            device TEXT NOT NULL,
            type TEXT NOT NULL,
            value REAL,
            note TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS ix_events_device_ts ON events(device, ts)",
        """CREATE TABLE IF NOT EXISTS calibration (
            device TEXT PRIMARY KEY,
            dry_raw INTEGER NOT NULL,
            wet_raw INTEGER NOT NULL,
            updated REAL NOT NULL
        )""",
        # Команда полива, ожидающая следующей телеметрии устройства
        """CREATE TABLE IF NOT EXISTS pending_commands (
            device TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            seconds INTEGER NOT NULL,
            created REAL NOT NULL,
            note TEXT
        )""",
    ],
    # 2 — реестр моделей (M1/M2): на каких данных обучена, метрики, рабочая версия
    [
        """CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            version TEXT NOT NULL,
            created REAL NOT NULL,
            metrics_json TEXT NOT NULL,
            is_champion INTEGER NOT NULL DEFAULT 0,
            trained_on_json TEXT NOT NULL DEFAULT '{}',
            path TEXT NOT NULL DEFAULT ''
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_models_task_version ON models(task, version)",
    ],
]


class Database:
    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def version(self) -> int:
        with self.conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            row = c.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            return int(row["value"]) if row else 0

    def migrate(self) -> int:
        current = self.version()
        for n, statements in enumerate(MIGRATIONS[current:], start=current + 1):
            with self.conn() as c:
                for sql in statements:
                    c.execute(sql)
                c.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(n),),
                )
        return len(MIGRATIONS)
