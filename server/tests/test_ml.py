"""ML (M0/M1): синтетические ряды с известным ответом.

Известны скорость высыхания, поливы и время до порога.
"""

import time

import numpy as np
import pandas as pd
import pytest

from app.ml import evaluate as ev
from app.ml import model as fm
from app.ml import registry
from app.ml.datasets import frame, mendeley, projectplant, thingspeak
from app.ml.forecast import consistent_hours
from app.ml.metrics import beats, improvement, mae, time_to_threshold, ttt_errors
from app.ml.prepare import cycles, normalize, prepare, quality, to_grid
from app.ml.train import train

from .conftest import TELEMETRY


def sawtooth(rate=0.5, top=80.0, low=30.0, days=20, step_min=15, noise=0.0, source="syn",
             series="syn/a", seed=0, start="2024-01-01"):
    """Почва сохнет с rate %/ч от top до low, затем полив обратно до top."""
    rng = np.random.default_rng(seed)
    n = int(days * 24 * 60 / step_min)
    ts = pd.date_range(start, periods=n, freq=f"{step_min}min", tz="UTC")
    soil, s = [], top
    for _ in range(n):
        soil.append(s)
        s -= rate * step_min / 60
        if s < low:
            s = top
    soil = np.array(soil) + rng.normal(0, noise, n)
    return frame(ts, source, series, soil_pct=soil, temp=22 + rng.normal(0, 0.3, n),
                 air_hum=50 + rng.normal(0, 1, n))


def period_h(rate=0.5, top=80.0, low=30.0):
    return (top - low) / rate


# ----- метрики (6.1, 6.6) -----

def test_metrics_known_answers():
    assert mae([1, 2, 3], [2, 2, 5]) == pytest.approx(1.0)
    assert time_to_threshold([50, 20, 50], [-2, -1, 0.5], 30).tolist() == [10, 0, 24 * 14]
    e = ttt_errors([10, 20, 30, 40], [11, 26, 30, 39])
    assert e["ttt_mae_h"] == 2.0 and e["ttt_median_h"] == 1.0 and e["ttt_within_3h"] == 0.75
    assert improvement(5, 10) == 0.5


def test_champion_rule():
    champ = {"ttt_mae_h": 10.0, "mae_6h": 2.0}
    assert beats({"ttt_mae_h": 9.4, "mae_6h": 2.0}, champ)
    assert not beats({"ttt_mae_h": 9.6, "mae_6h": 2.0}, champ)      # лучше меньше чем на 5 %
    assert not beats({"ttt_mae_h": 5.0, "mae_6h": 2.1}, champ)      # MAE 6 ч хуже
    assert beats({"ttt_mae_h": 9.0, "mae_6h": 1.0}, {"ttt_mae_h": None})


# ----- подготовка (M0) -----

def test_inferred_watering_and_cycles():
    df = prepare(sawtooth(rate=1.0, days=10))
    expected = int(10 * 24 / period_h(1.0))
    assert df["watering_inferred"].all()
    assert abs(int(df["watering_event"].sum()) - expected) <= 1
    assert abs(len(cycles(df)) - (expected - 1)) <= 1


def test_normalize_inverse_raw_adc():
    raw = frame(pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC"), "s", "s",
                soil_raw=np.linspace(1000, 3000, 100))
    df = normalize(to_grid(raw))
    assert df["soil"].iloc[0] == pytest.approx(100, abs=1)    # мало АЦП = мокро
    assert df["soil"].iloc[-1] == pytest.approx(0, abs=1)


def test_quality_rejects_series_without_continuity():
    good = sawtooth(rate=1.0, days=10)
    rng = np.random.default_rng(1)
    noise = frame(good["ts"], "n", "n", soil_pct=rng.uniform(0, 100, len(good)))
    assert quality(good, prepare(good)).usable
    q = quality(noise, prepare(noise))
    assert not q.usable and "непрерывности" in q.reason


def test_samples_targets_match_known_rate():
    s = fm.samples(prepare(sawtooth(rate=0.5, days=15)))
    d6 = s["d6"].dropna()
    # нормализация растягивает 30…80 % на 0…100 → скорость в 2 раза больше
    assert d6.median() == pytest.approx(-0.5 * 2 * 6, rel=0.05)
    row = s.dropna(subset=["ttt_true"])
    row = row[row["soil"] > row["threshold"] + 20].iloc[0]
    # перцентильная нормализация растягивает чуть сильнее — допуск 5 %
    assert row["ttt_true"] == pytest.approx(row["soil"] - row["threshold"], rel=0.05)


# ----- модель и адаптация (M1, 6.3) -----

def synthetic_data():
    parts = [fm.samples(prepare(sawtooth(rate=r, days=24, noise=0.8, seed=i, source=src,
                                         series=f"{src}/{i}")))
             for i, (r, src) in enumerate([(0.4, "a"), (0.8, "a"), (0.6, "b"), (1.0, "b")])]
    return pd.concat(parts, ignore_index=True)


def test_model_beats_linear_baseline_on_noisy_series():
    r = ev.time_split(synthetic_data(), "gbr")
    assert r["model_ttt_mae_h"] < r["linear_ttt_mae_h"]
    assert r["model_mae_6h"] < r["linear_mae_6h"]


def test_adaptation_recovers_slower_plant():
    train_data = fm.samples(prepare(sawtooth(rate=1.0, days=20, noise=0.3)))
    model = fm.ForecastModel("gbr").fit(train_data)
    # «Своё» растение сохнет в 2 раза медленнее, но высыхает так же 30→80 → нормализация та же
    own = fm.samples(prepare(sawtooth(rate=0.5, days=20, noise=0.3, series="own")))
    scale = fm.fit_scale(model, own, prior_cycles=0)
    assert scale == pytest.approx(2.0, rel=0.2)
    test = fm.ttt_rows(own)
    before = ttt_errors(test["ttt_true"], model.ttt(test))["ttt_mae_h"]
    after = ttt_errors(test["ttt_true"], model.ttt(test, scale))["ttt_mae_h"]
    assert after < before / 2


def test_consistent_hours_uses_trajectory_crossing():
    traj = [{"h": 0, "soil": 40}, {"h": 6, "soil": 36}, {"h": 12, "soil": 32},
            {"h": 24, "soil": 28}]
    assert consistent_hours(traj, 30, 80) == pytest.approx(18)
    assert consistent_hours(traj[:2], 30, 5) == 6       # не пересекает за 6 ч → не меньше 6


# ----- загрузчики источников -----

def test_projectplant_parser():
    text = ("Jeff\t * L\n\nFlower Care (AA)\nFicus elastica (Ficus)\t2020-07-15\t\t\t\t"
            "2020-07-16\t\t\t\n\tL\tS\tT\tE\tL\tS\tT\tE\n"
            "01:00\t--\t--\t--\t--\t1\t21\t23.1\t153\n"
            "00:00\t2\t20\t23.0\t150\t1\t19\t22.9\t140\n")
    [df] = projectplant.parse(text)
    assert df["series"].iloc[0] == "projectplant/ficus_elastica"
    assert df["soil_pct"].tolist() == [20, 21, 19]
    assert str(df["ts"].iloc[0]) == "2020-07-16 00:00:00+00:00"


def test_thingspeak_and_mendeley_loaders(tmp_path):
    (tmp_path / "feed.csv").write_text(
        "created_at,entry_id,field1,field2,field3,field4,field5,field6\n"
        "2025-01-01 00:00:00 UTC,1,40,50,22,500,,0\n"
        "2025-01-01 00:15:00 UTC,2,70,50,22,300,,1\n", encoding="utf-8")
    [ag] = thingspeak.load_aglaonema(tmp_path)
    assert ag["watering_event"].tolist() == [False, True] and not ag["watering_inferred"].any()
    [sm] = thingspeak.load_soil_monitor(tmp_path)
    assert sm["temp"].iloc[0] == pytest.approx((50 - 32) * 5 / 9)
    (tmp_path / mendeley.FILE).write_text(
        "Time,Humidity,Atmospheric_Temp,Soil_Temp,Soil_Moisture,Dew_Point\n"
        "2017-06-11 21:00:21+00:00,25,15,12,63,23\n", encoding="utf-8")
    [md] = mendeley.load(tmp_path)
    assert md["soil_pct"].iloc[0] == 63 and md["watering_inferred"].all()


# ----- реестр, обучение, прогноз -----

def test_registry_promotes_only_better_models(app, tmp_path):
    db = app.state.db
    good = {"beats_baseline": True, "ttt_mae_h": 10.0, "mae_6h": 2.0}
    assert registry.save(db, str(tmp_path), "t", {}, good, {}, "v1")["is_champion"]
    assert not registry.save(db, str(tmp_path), "t", {}, {**good, "ttt_mae_h": 9.8}, {},
                             "v2")["is_champion"]
    worse_than_baseline = {**good, "beats_baseline": False, "ttt_mae_h": 1.0}
    assert not registry.save(db, str(tmp_path), "t", {}, worse_than_baseline, {},
                             "v3")["is_champion"]
    assert registry.save(db, str(tmp_path), "t", {}, {**good, "ttt_mae_h": 9.0}, {},
                         "v4")["is_champion"]
    assert registry.champion(db, "t")["version"] == "v4"
    assert [m["version"] for m in registry.history(db, "t")] == ["v4", "v3", "v2", "v1"]


def add_device_history(device_client, hours=12, rate=1.0, start_soil=70):
    """Телеметрия «в прошлом» прямо в БД (API ставит ts = сейчас)."""
    db = device_client.app.state.db
    now = time.time()
    with db.conn() as c:
        for i in range(hours * 4):
            ts = now - hours * 3600 + i * 900
            c.execute("INSERT INTO measurements (ts, device, temp, air_hum, soil_raw, soil_pct, "
                      "soil_ok, soil_dry_d0) VALUES (?, 'plant1', 23, 45, 2500, ?, 1, 0)",
                      (ts, int(round(start_soil - rate * i / 4))))


def test_forecast_without_data_and_without_model(app, device, web):
    assert web.get("/web/api/forecast").json()["status"] == "insufficient_data"
    add_device_history(device)
    fc = web.get("/web/api/forecast").json()
    assert fc["status"] == "ok" and fc["method"] == "linear" and fc["model"] is None
    # 59 % сейчас, сохнет 1 %/ч → до 30 % ≈ 29 ч
    assert fc["hours_to_threshold"] == pytest.approx(29, abs=2)
    assert device.get("/api/forecast").json()["status"] == "ok"


def test_train_registers_model_and_forecast_uses_it(app, device, web, tmp_path):
    data = synthetic_data()
    result = train(app.state.db, str(tmp_path), data,
                   {"sources": ["a", "b"], "series": [], "own_cycles": 0},
                   report_path=tmp_path / "report.md")
    assert result["is_champion"] and result["metrics"]["beats_baseline"]
    assert "Время до порога" in (tmp_path / "report.md").read_text(encoding="utf-8")
    add_device_history(device)
    fc = web.get("/web/api/forecast").json()
    assert fc["method"] == "model" and fc["model"]["trained_on"]["sources"] == ["a", "b"]
    assert len(fc["trajectory"]) == 4 and fc["hours_to_threshold"] > 0


def test_llm_context_includes_forecast(app, device):
    from app.llm.context import describe
    device.post("/api/telemetry", json=TELEMETRY)
    assert "Прогноз" in describe(app.state.db, "plant1")


# ----- симуляция политик (6.4) -----

def test_policy_simulation_threshold_vs_ml():
    from app.ml import policy_sim
    df = prepare(sawtooth(rate=1.0, days=12, noise=0.2))
    thr = policy_sim.simulate(df, "threshold")
    ml = policy_sim.simulate(df, "ml")                   # без модели — линейный прогноз
    assert thr["mean_soil"] < 40 < ml["mean_soil"]       # порог держит почву у края
    assert ml["waterings"] < thr["waterings"] / 2
    assert ml["in_range_share"] > 0.9 and ml["hours_wet"] == 0
    total = policy_sim.compare([df], None)["total"]
    assert total["ml"]["waterings"] == ml["waterings"]


# ----- MLOps (M2) -----

def test_forecast_monitoring_resolves_actual_error(app, device):
    from app.ml import monitor
    db = app.state.db
    add_device_history(device, hours=12, rate=1.0, start_soil=70)   # сейчас 59 %, 1 %/ч
    assert monitor.log_forecast(db, "plant1")
    assert not monitor.log_forecast(db, "plant1")                   # не чаще раза в час
    with db.conn() as c:
        c.execute("UPDATE forecast_log SET ts = ts - 7200")
        c.execute("INSERT INTO measurements (ts, device, soil_pct, soil_ok) "
                  "VALUES (?, 'plant1', 29, 1)", (time.time(),))
    assert monitor.resolve(db, "plant1") == 1
    err = monitor.actual_error(db)
    assert err["checked"] == 1 and err["ttt_mae_h"] is not None


def test_own_data_joins_training_after_three_cycles(app):
    from app.ml.train import with_own_data
    db = app.state.db
    df = sawtooth(rate=2.0, days=8)                      # 25 ч на цикл → 7 циклов
    with db.conn() as c:
        for ts, pct in zip(df["ts"], df["soil_pct"], strict=False):
            c.execute("INSERT INTO measurements (ts, device, temp, air_hum, soil_raw, soil_pct, "
                      "soil_ok, soil_dry_d0) VALUES (?, 'plant1', 22, 50, 2000, ?, 1, 0)",
                      (ts.timestamp(), int(pct)))
        prev = None
        for ts, pct in zip(df["ts"], df["soil_pct"], strict=False):
            if prev is not None and pct > prev + 20:
                c.execute("INSERT INTO events (ts, device, type, value, note) "
                          "VALUES (?, 'plant1', 'water', 3, 'auto')", (ts.timestamp(),))
            prev = pct
    base = synthetic_data()
    now = df["ts"].iloc[-1].timestamp()
    data, trained_on = with_own_data(db, base, {"sources": ["a", "b"], "series": [],
                                                "own_cycles": 0}, now=now)
    assert "device" in trained_on["sources"] and trained_on["own_cycles"] >= 3
    assert (data["source"] == "device").sum() > 0


def test_model_card_endpoint(app, web, tmp_path):
    assert web.get("/web/api/model").json()["champion"] is None
    train(app.state.db, str(tmp_path), synthetic_data(),
          {"sources": ["a", "b"], "series": [], "own_cycles": 0})
    card = web.get("/web/api/model").json()
    assert card["champion"]["metrics"]["ttt_mae_h"] is not None
    assert card["history"][0]["is_champion"] and card["monitoring"]["checked"] == 0


# ----- дашборд (W1): открытый датасет как источник, отметки поливов -----

def test_demo_source_endpoints(app, web, tmp_path, monkeypatch):
    from app.ml import demo
    folder = tmp_path / "thingspeak_soil_monitor"
    folder.mkdir()
    df = sawtooth(rate=1.0, days=10)
    raw = (3000 - df["soil_pct"] * 20).round().astype(int)
    lines = ["created_at,entry_id,field1,field2,field3,field4"] + [
        f"{t:%Y-%m-%d %H:%M:%S} UTC,{i},{r},72,29.8,50" for i, (t, r) in
        enumerate(zip(df["ts"], raw, strict=False))]
    (folder / "feed.csv").write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setenv("PLANT_EXTERNAL_DIR", str(tmp_path))
    demo._cache.clear()
    assert web.get("/web/api/demo/series").json() == ["soil_monitor"]
    d = web.get("/web/api/demo?series=soil_monitor&hours=48").json()
    assert abs(d["history"][-1]["ts"] - time.time()) < 60                 # конец ряда = сейчас
    assert d["history"][-1]["temp"] == pytest.approx((72 - 32) * 5 / 9, abs=0.1)
    assert d["waterings"] and d["forecast"] is None                       # модели ещё нет
    assert web.get("/web/api/demo?series=nope").status_code == 404


def test_waterings_endpoint(app, device, web):
    device.post("/api/event", json={"device": "plant1", "type": "water", "value": 3,
                                    "note": "claps"})
    assert web.get("/web/api/waterings?hours=1").json()[0]["note"] == "claps"
