"""Калибровка под растение (C1): виртуальный горшок с известными raw_dry, raw_wet, k_dose, dry_rate.

Симулятор ведёт себя как устройство: шлёт показания раз в минуту, выполняет команды полива,
сообщает о поливе. Готово по плану — параметры восстанавливаются с ошибкой ≤ 10 %.
"""

import time

import numpy as np
import pytest

from app import calibration as cb
from app import safety, store

from .conftest import TELEMETRY

RAW_DRY, RAW_WET = 3800, 1400
K_DOSE = 4.0          # % за секунду насоса
DRY_RATE = 2.0        # %/ч
DELAY_S = 180         # вода доходит до датчика через 3 мин
SPREAD_S = 360        # и впитывается за 6 мин


class Pot:
    def __init__(self, db, device="plant1", soil=0.0, start=None, seed=0):
        self.db, self.device, self.soil = db, device, soil
        self.t = start or time.time() - 10 * 86400
        self.rng = np.random.default_rng(seed)
        self.inflows: list[tuple[float, float]] = []     # (начало, сколько %)
        self.pump_ok = True
        self.log = []

    def raw(self) -> int:
        return int(RAW_DRY - self.soil / 100 * (RAW_DRY - RAW_WET) + self.rng.normal(0, 8))

    def water(self, seconds: float, note: str = "web"):
        if self.pump_ok:
            self.inflows.append((self.t + DELAY_S, K_DOSE * seconds))
        with self.db.conn() as c:
            c.execute("INSERT INTO events (ts, device, type, value, note) VALUES (?, ?, 'water', "
                      "?, ?)", (self.t + seconds, self.device, seconds, note))

    def tick(self, dt=60):
        self.t += dt
        self.soil = max(0.0, self.soil - DRY_RATE * dt / 3600)
        for start, amount in self.inflows:
            # доля впитывания за шаг = пересечение [t − dt, t] с [start, start + SPREAD_S]
            overlap = min(self.t, start + SPREAD_S) - max(self.t - dt, start)
            if overlap > 0:
                self.soil = min(100.0, self.soil + amount * overlap / SPREAD_S)
        raw = self.raw()
        cal = store.get_calibration(self.db, self.device)
        pct = cb.percent(raw, cal["dry_raw"], cal["wet_raw"]) if cal else cb.percent(raw, 4000,
                                                                                         1150)
        with self.db.conn() as c:
            c.execute("INSERT INTO measurements (ts, device, temp, air_hum, soil_raw, soil_pct, "
                      "soil_ok, soil_dry_d0) VALUES (?, ?, 23, 45, ?, ?, 1, 0)",
                      (self.t, self.device, raw, pct))
        r = cb.on_telemetry(self.db, self.device, now=self.t)
        if r:
            self.log.append((self.t, r))
        cmd = store.take_command(self.db, self.device, 120)
        if cmd:
            self.water(cmd["seconds"], cmd["note"])
        return r

    def run(self, hours, until=None):
        for _ in range(int(hours * 60)):
            r = self.tick()
            if until and r == until:
                return True
        return False


def full_calibration(db) -> Pot:
    pot = Pot(db)
    cb.set_profile(db, "plant1", "moderate")
    pot.run(0.25)
    cb.mark_dry(db, "plant1", now=pot.t)
    pot.soil = 100.0                                     # полный пролив
    cb.start_wet(db, "plant1", now=pot.t)
    assert pot.run(1, until="wet")
    assert pot.run(40, until="dose")
    assert pot.run(12, until="dry_rate")
    return pot


def test_full_calibration_recovers_parameters(app):
    db = app.state.db
    pot = full_calibration(db)
    cal = cb.get(db, "plant1")
    assert cal["stage"] == 4 and cal["confidence"] == "low"
    assert cal["raw_dry"] == pytest.approx(RAW_DRY, rel=0.1)
    assert cal["raw_wet"] == pytest.approx(RAW_WET, rel=0.1)
    assert cal["dry_raw"] == cal["raw_dry"] and cal["wet_raw"] == cal["raw_wet"]
    assert cal["k_dose"] == pytest.approx(K_DOSE, rel=0.1)
    assert cal["dry_rate"] == pytest.approx(DRY_RATE, rel=0.1)
    assert 2 <= len(cal["state"]["pulses"]) <= 4
    assert cal["response_delay_min"] == pytest.approx(DELAY_S / 60 + 1.5, abs=1.5)
    with db.conn() as c:
        notes = [r["note"] for r in c.execute("SELECT note FROM events WHERE type = 'water'")]
    assert notes and set(notes) == {"calib"}                # импульсы — через слой безопасности
    # Доза: (верх диапазона − текущая) / k_dose, не больше 10 с
    assert cb.dose_seconds(db, "plant1", 45) == round((65 - 45) / cal["k_dose"])
    assert cb.dose_seconds(db, "plant1", 0) == 10
    assert pot.log


def test_adaptation_and_anomaly(app):
    db = app.state.db
    pot = full_calibration(db)
    k0 = cb.get(db, "plant1")["k_dose"]
    pot.run(1)
    pot.water(3, "voice")
    assert pot.run(2, until="adapted")
    cal = cb.get(db, "plant1")
    assert cal["cycles"] == 1
    assert cal["k_dose"] == pytest.approx(0.8 * k0 + 0.2 * K_DOSE, rel=0.05)
    # Насос сломался: полив был, влажность не выросла → аномалия, сервер не поливает
    pot.pump_ok = False
    pot.run(1)
    pot.water(3, "voice")
    assert pot.run(2, until="anomaly")
    v = safety.check(db, "plant1", 3, now=pot.t)
    assert not v.ok and "аномалия" in v.reason
    assert "Аномалия" in cb.hint(cb.get(db, "plant1"))
    cb.confirm_anomaly(db, "plant1")
    assert cb.anomaly_active(db, "plant1") is None


def test_sensor_too_close_points(app):
    db = app.state.db
    pot = Pot(db, soil=60)
    cb.set_profile(db, "plant1", "moderate")
    pot.run(0.25)
    cb.mark_dry(db, "plant1", now=pot.t)                  # «сухо» на самом деле влажно
    pot.soil = 70
    cb.start_wet(db, "plant1", now=pot.t)
    assert pot.run(1, until="error")
    assert "переставьте датчик" in cb.hint(cb.get(db, "plant1"))


def test_dry_point_requires_stable_readings(app):
    db = app.state.db
    pot = Pot(db, soil=0)
    cb.set_profile(db, "plant1", "moderate")
    with pytest.raises(ValueError, match="10 минут"):
        cb.mark_dry(db, "plant1", now=pot.t)
    for _ in range(12):
        pot.soil = 0 if pot.soil else 30                    # скачет
        pot.tick()
    with pytest.raises(ValueError, match="меняются"):
        cb.mark_dry(db, "plant1", now=pot.t)


def test_profiles_and_validation(app):
    db = app.state.db
    cal = cb.set_profile(db, "plant1", "hygrophilous")
    assert (cal["target_low"], cal["target_high"], cal["stage"]) == (50, 80, 1)
    assert cb.set_profile(db, "plant1", "xerophytic", 15, 35)["target_high"] == 35
    with pytest.raises(ValueError):
        cb.set_profile(db, "plant1", "cactus")
    with pytest.raises(ValueError):
        cb.set_profile(db, "plant1", "moderate", 60, 40)


def test_fit_dry_rate_with_temperature():
    t0 = 0.0
    rows = [{"ts": t0 + i * 600, "soil_pct": 70 - 1.5 * i / 6 + 0.5 * np.sin(i),
             "temp": 22 + np.sin(i), "air_hum": 40.0 + i % 3} for i in range(60)]
    assert cb.fit_dry_rate(rows) == pytest.approx(1.5, rel=0.1)
    assert cb.fit_dry_rate(rows[:10]) is None             # меньше 6 ч


def test_telemetry_reports_stage_and_server_percent(app, device, web):
    r = web.post("/web/api/calibration/profile", json={"plant_type": "moderate"})
    assert r.json()["stage"] == 1
    resp = device.post("/api/telemetry", json=TELEMETRY).text
    assert '"calib_stage":1' in resp and '"dry":4000' in resp       # компактный JSON
    store.set_calibration(app.state.db, "plant1", 3000, 1000)
    device.post("/api/telemetry", json={**TELEMETRY, "soil_raw": 2000, "soil_pct": 13})
    assert store.latest_measurement(app.state.db, "plant1")["soil_pct"] == 50
    state = web.get("/web/api/calibration/state").json()
    assert state["calibration"]["stage"] == 1 and state["profiles"]["moderate"]["high"] == 65
    assert "Шаг 1" in state["hint"]
    # Ручная калибровка не стирает новые поля
    assert cb.get(app.state.db, "plant1")["plant_type"] == "moderate"


def test_calibration_web_flow_errors(app, web):
    assert web.post("/web/api/calibration/dry").status_code == 409
    assert web.post("/web/api/calibration/wet").status_code == 409
    assert web.post("/web/api/calibration/profile", json={"plant_type": "x"}).status_code == 400
    assert web.post("/web/api/calibration/reset").json()["calibration"]["stage"] == 0
