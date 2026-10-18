import csv
import io
import sqlite3

from app.db import MIGRATIONS, Database

from .conftest import TELEMETRY


def test_migrations_set_version_and_are_idempotent(tmp_path):
    db = Database(str(tmp_path / "p.db"))
    assert db.migrate() == len(MIGRATIONS)
    assert db.version() == len(MIGRATIONS)
    db.migrate()
    assert db.version() == len(MIGRATIONS)


def test_migration_keeps_data_of_old_database(tmp_path):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, "
              "device TEXT, temp REAL, air_hum REAL, soil_raw INTEGER, soil_pct INTEGER, "
              "soil_ok INTEGER, soil_dry_d0 INTEGER)")
    c.execute("INSERT INTO measurements (ts, device, soil_raw) VALUES (1, 'plant1', 3000)")
    c.commit()
    c.close()
    db = Database(path)
    db.migrate()
    with db.conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 1


def test_state_and_history(device, web):
    device.post("/api/telemetry", json=TELEMETRY)
    s = web.get("/web/api/state").json()
    assert s["latest"]["soil_pct"] == 50
    assert web.get("/web/api/history?hours=1").json()[0]["soil_raw"] == 2500
    assert web.get("/web/api/devices").json() == ["plant1"]


def test_calibration_from_current_value(device, web):
    device.post("/api/telemetry", json={**TELEMETRY, "soil_raw": 3950})
    assert web.post("/web/api/calibration", json={"use_current": "dry"}).json()["dry_raw"] == 3950
    device.post("/api/telemetry", json={**TELEMETRY, "soil_raw": 1200})
    r = web.post("/web/api/calibration", json={"use_current": "wet"}).json()
    assert r == {"dry_raw": 3950, "wet_raw": 1200}


def test_calibration_validation(web):
    assert web.post("/web/api/calibration",
                    json={"dry_raw": 1000, "wet_raw": 3000}).status_code == 400
    assert web.post("/web/api/calibration", json={"use_current": "dry"}).status_code == 400


def test_csv_export(device, web):
    device.post("/api/telemetry", json=TELEMETRY)
    device.post("/api/event", json={"device": "plant1", "type": "boot", "value": 0,
                                    "note": "start"})
    rows = list(csv.reader(io.StringIO(web.get("/web/api/export.csv").text)))
    assert rows[0][:3] == ["id", "ts", "device"] and len(rows) == 2
    rows = list(csv.reader(io.StringIO(web.get("/web/api/export.csv?table=events").text)))
    assert rows[1][3] == "boot"
    assert web.get("/web/api/export.csv?table=calibration").status_code == 400
