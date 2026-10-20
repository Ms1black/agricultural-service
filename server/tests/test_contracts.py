"""Контракты 4.1–4.2: прошивка ищет подстроки в ответе, поэтому проверяем байты."""

import time

from app import store

from .conftest import TELEMETRY


def test_telemetry_no_command(device):
    r = device.post("/api/telemetry", json=TELEMETRY)
    assert r.status_code == 200
    assert r.content == b'{"command":"none"}'


def test_telemetry_water_command_exact_format(device, web):
    web.post("/web/api/calibration", json={"device": "plant1", "dry_raw": 3900, "wet_raw": 1300})
    web.post("/web/api/water", json={"device": "plant1", "seconds": 3})
    r = device.post("/api/telemetry", json=TELEMETRY)
    assert r.content == b'{"command":"water","seconds":3,"dry":3900,"wet":1300}'


def test_water_command_is_delivered_once(device, web):
    web.post("/web/api/water", json={"device": "plant1", "seconds": 2})
    assert b'"command":"water"' in device.post("/api/telemetry", json=TELEMETRY).content
    assert device.post("/api/telemetry", json=TELEMETRY).content == b'{"command":"none"}'


def test_water_command_only_for_its_device(device, web):
    web.post("/web/api/water", json={"device": "plant2", "seconds": 2})
    assert device.post("/api/telemetry", json=TELEMETRY).content == b'{"command":"none"}'


def test_expired_water_command_is_dropped(app, device):
    store.queue_water(app.state.db, "plant1", 3)
    with app.state.db.conn() as c:
        c.execute("UPDATE pending_commands SET created = ?", (time.time() - 600,))
    assert device.post("/api/telemetry", json=TELEMETRY).content == b'{"command":"none"}'


def test_water_seconds_capped_at_10(device, web):
    assert web.post("/web/api/water", json={"seconds": 60}).json()["seconds"] == 10
    assert b'"seconds":10' in device.post("/api/telemetry", json=TELEMETRY).content


def test_calibration_sent_without_command(device, web):
    web.post("/web/api/calibration", json={"dry_raw": 4000, "wet_raw": 1150})
    r = device.post("/api/telemetry", json=TELEMETRY)
    assert r.content == b'{"command":"none","dry":4000,"wet":1150}'


def test_telemetry_is_stored(app, device):
    device.post("/api/telemetry", json=TELEMETRY)
    m = store.latest_measurement(app.state.db, "plant1")
    assert m["soil_raw"] == 2500 and m["soil_pct"] == 50 and m["soil_ok"] == 1
    assert m["soil_dry_d0"] == 0 and m["temp"] == 24.5 and m["air_hum"] == 55.2


def test_telemetry_accepts_null_sensors_and_extra_fields(device):
    body = {**TELEMETRY, "temp": None, "air_hum": None, "soil_pct": -1, "soil_ok": False,
            "uptime_s": 120, "fw_version": "1.0"}
    assert device.post("/api/telemetry", json=body).status_code == 200


def test_event_is_stored(app, device):
    r = device.post("/api/event",
                    json={"device": "plant1", "type": "water", "value": 3.0, "note": "auto"})
    assert r.status_code == 200
    ev = store.recent_events(app.state.db, "plant1")
    assert ev[0]["type"] == "water" and ev[0]["value"] == 3.0 and ev[0]["note"] == "auto"


def test_buffered_telemetry_uses_age_and_skips_decisions(app, device):
    import time

    from app import store
    r = device.post("/api/telemetry", json={**TELEMETRY, "age_s": 3600, "uptime_s": 10,
                                            "fw_version": "1.1"})
    assert r.text == '{"command":"none"}'
    m = store.latest_measurement(app.state.db, "plant1")
    assert abs(m["ts"] - (time.time() - 3600)) < 5


def test_next_water_hours_in_response(app, device):
    from app.ml import forecast as fcm

    from .test_ml import add_device_history
    add_device_history(device, hours=12, rate=1.0, start_soil=70)      # 59 %, 1 %/ч → ≈ 29 ч
    fcm._cache.clear()
    r = device.post("/api/telemetry", json={**TELEMETRY, "soil_pct": 58})
    assert '"next_water_h":' in r.text and " " not in r.text            # компактный JSON
