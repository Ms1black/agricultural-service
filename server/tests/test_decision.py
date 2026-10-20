"""Модуль решений (D0): режимы, доступность ml, решение по прогнозу через слой безопасности."""

import time

from app import decision, store
from app.llm.context import describe

from .conftest import TELEMETRY


def calibrate(db, confidence="medium"):
    store.set_calibration(db, "plant1", 3800, 1400)
    with db.conn() as c:
        c.execute("UPDATE calibration SET stage = 4, confidence = ?, k_dose = 4.0, "
                  "target_low = 35, target_high = 65, cycles = 3", (confidence,))


def drying_history(db, start=37.0, rate=4.0, hours=8):
    """Почва сохнет rate %/ч и сейчас почти у нижней границы 35 %."""
    now = time.time()
    with db.conn() as c:
        for i in range(hours * 4 + 1):
            ts = now - hours * 3600 + i * 900
            pct = start + rate * hours - rate * i / 4
            raw = int(3800 - pct / 100 * 2400)
            c.execute("INSERT INTO measurements (ts, device, temp, air_hum, soil_raw, soil_pct, "
                      "soil_ok, soil_dry_d0) VALUES (?, 'plant1', 23, 45, ?, ?, 1, 0)",
                      (ts, raw, int(round(pct))))


def test_default_mode_and_ml_unavailable_without_calibration(app, device, web):
    assert web.get("/web/api/mode").json()["mode"] == "threshold"
    r = web.post("/web/api/mode", json={"mode": "ml"}).json()
    assert r["mode"] == "ml" and r["active"] == "threshold" and not r["ml_available"]
    assert '"mode"' not in device.post("/api/telemetry", json=TELEMETRY).text
    assert web.post("/web/api/mode", json={"mode": "auto"}).status_code == 400
    assert "недоступен" in web.get("/web/api/decisions").json()[0]["reason"]


def test_ml_mode_tells_device_and_waters_by_forecast(app, device, web):
    db = app.state.db
    calibrate(db)
    drying_history(db)
    decision.set_mode(db, "plant1", "ml")
    r = device.post("/api/telemetry", json={**TELEMETRY, "soil_raw": int(3800 - 0.36 * 2400)})
    assert '"mode":"ml"' in r.text and '"command":"water"' in r.text
    d = web.get("/web/api/decisions").json()[0]
    assert d["action"] == "water" and d["mode"] == "ml" and "прогноз" in d["reason"]
    assert d["seconds"] == round((65 - 36) / 4.0)               # доза из калибровки
    # Следующая телеметрия: интервал 30 мин не прошёл → запрет, записан один раз
    device.post("/api/event", json={"device": "plant1", "type": "water", "value": 7,
                                    "note": "web"})
    assert store.recent_events(db, "plant1")[0]["note"] == "ml"
    for _ in range(3):
        r = device.post("/api/telemetry", json={**TELEMETRY, "soil_raw": int(3800 - 0.35 * 2400)})
        assert '"command":"water"' not in r.text
    blocked = [x for x in decision.recent(db, "plant1") if x["action"] == "blocked"]
    assert len(blocked) == 1 and "недавно" in blocked[0]["reason"]


def test_ml_needs_medium_confidence(app):
    calibrate(app.state.db, confidence="low")
    decision.set_mode(app.state.db, "plant1", "ml")
    assert decision.active_mode(app.state.db, "plant1") == "threshold"
    assert decision.step(app.state.db, "plant1") is None


def test_llm_context_has_mode_and_decisions(app, device):
    db = app.state.db
    calibrate(db)
    decision.set_mode(db, "plant1", "ml")
    device.post("/api/telemetry", json=TELEMETRY)
    text = describe(db, "plant1")
    assert "Режим полива: ml" in text and "Последние решения сервера" in text
