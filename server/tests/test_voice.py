"""Голос (V1, V1b) и слой безопасности: STT и TTS подменены заглушками."""

import time
from urllib.parse import unquote

import numpy as np
import pytest

from app import safety, store
from app.voice import intent
from app.voice.audio import RATE, float_to_pcm16, read_wav, speech_text, wav_bytes

from .conftest import TELEMETRY

PHRASE = float_to_pcm16(np.zeros(RATE, dtype=np.float32))     # 1 с тишины


class FakeSTT:
    def __init__(self, text):
        self.text, self.seen = text, []

    def transcribe(self, audio):
        self.seen.append(audio)
        return self.text


class FakeTTS:
    def __init__(self):
        self.texts = []

    def synthesize(self, text):
        self.texts.append(text)
        return np.zeros(RATE // 2, dtype=np.float32)              # 0.5 с


class FakeLLM:
    name = "fake"

    def __init__(self, answer="Цветок в порядке."):
        self.answer, self.calls = answer, []

    def chat(self, messages):
        self.calls.append(messages)
        return self.answer


@pytest.fixture
def voice_app(app):
    app.state.tts = FakeTTS()
    return app


def say(app, client, text, path="/api/voice", **kw):
    app.state.stt = FakeSTT(text)
    return client.post(path, content=PHRASE, **kw)


# ----- намерения и текст для синтеза -----

@pytest.mark.parametrize("text,kind", [
    ("Джарвис, полей цветок", "water"),
    ("Jarvis, полить растение", "water"),
    ("Джарвис, как там мой цветок?", "status"),
    ("Что с растением?", "status"),
    ("Джарвис, не поливай", "chat"),
    ("Расскажи анекдот про кактус", "status"),
    ("Сколько будет два плюс два", "chat"),
    ("Джарвис", "empty"),
])
def test_intent(text, kind):
    assert intent(text) == kind


def test_speech_text_numbers_units_and_markup():
    t = speech_text("**Влажность** 50 %, температура 24.5 °C, до полива ≈ 18 ч. См. https://x.y")
    assert "пятьдесят процентов" in t and "двадцать четыре градусов" in t
    assert "примерно восемнадцать часов" in t
    assert not any(ch.isdigit() for ch in t) and "*" not in t and "http" not in t


def test_wav_roundtrip():
    pcm = float_to_pcm16(np.linspace(-0.5, 0.5, RATE, dtype=np.float32))
    assert len(read_wav(wav_bytes(pcm))) == RATE


# ----- слой безопасности (раздел 5) -----

def test_safety_rules(app, device):
    db = app.state.db
    assert not safety.check(db, "plant1", 3).ok                       # нет данных
    device.post("/api/telemetry", json={**TELEMETRY, "soil_pct": 75})
    v = safety.check(db, "plant1", 3)
    assert not v.ok and "влажная" in v.reason                        # > 70 %
    device.post("/api/telemetry", json={**TELEMETRY, "soil_pct": 40})
    assert safety.check(db, "plant1", 30).seconds == 10              # максимум 10 с
    store.save_event(db, "plant1", "water", 3, "voice")
    v = safety.check(db, "plant1", 3)
    assert not v.ok and "недавно" in v.reason                        # интервал 30 мин
    old = time.time() - 3600
    with db.conn() as c:
        c.execute("UPDATE events SET ts = ?", (old,))
        for _ in range(5):
            c.execute("INSERT INTO events (ts, device, type, value, note) "
                      "VALUES (?, 'plant1', 'water', 3, 'auto')", (old,))
    v = safety.check(db, "plant1", 3)
    assert not v.ok and "максимум" in v.reason                       # 6 в сутки


def test_safety_rejects_stale_data(app, device):
    device.post("/api/telemetry", json=TELEMETRY)
    assert not safety.check(app.state.db, "plant1", 3, now=time.time() + 300).ok


def test_command_reason_relabels_firmware_web_note(app, device):
    device.post("/api/telemetry", json={**TELEMETRY, "soil_pct": 40})
    assert safety.request_water(app.state.db, "plant1", 3, "voice").ok
    assert '"command":"water"' in device.post("/api/telemetry", json=TELEMETRY).text
    device.post("/api/event", json={"device": "plant1", "type": "water", "value": 3,
                                    "note": "web"})
    assert store.recent_events(app.state.db, "plant1")[0]["note"] == "voice"


# ----- /api/voice (контракт 4.3) -----

def test_voice_disabled_is_503(device):
    assert device.post("/api/voice", content=PHRASE).status_code == 503


def test_voice_status_without_llm_uses_template(voice_app, device, web):
    device.post("/api/telemetry", json=TELEMETRY)
    r = say(voice_app, device, "Джарвис, как там мой цветок?", headers={"X-Device": "plant1"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/L16")
    assert len(r.content) == RATE                                     # 0.5 с × 2 байта
    assert unquote(r.headers["X-Transcript"]) == "Джарвис, как там мой цветок?"
    answer = unquote(r.headers["X-Answer"])
    assert "Влажность почвы 50 %" in answer
    assert voice_app.state.tts.texts[-1].startswith("Влажность почвы пятьдесят процентов")
    vid = int(r.headers["X-Answer-Id"])
    wav = web.get(f"/web/api/voice/{vid}.wav")
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
    item = web.get("/web/api/voice/log").json()["items"][0]
    assert item["id"] == vid and item["intent"] == "status" and item["ms_stt"] >= 0


def test_voice_chat_goes_to_llm_with_speech_hint(voice_app, device):
    voice_app.state.llm = FakeLLM("Всё хорошо.")
    r = say(voice_app, device, "Джарвис, как там мой цветок?")
    assert unquote(r.headers["X-Answer"]) == "Всё хорошо."
    system = voice_app.state.llm.calls[0][0]["content"]
    assert "произнесён вслух" in system


def test_voice_water_goes_through_safety(voice_app, device):
    device.post("/api/telemetry", json={**TELEMETRY, "soil_pct": 75})
    r = say(voice_app, device, "Джарвис, полей")
    assert unquote(r.headers["X-Answer"]).startswith("Не буду поливать: почва достаточно влажная")
    assert store.pending_command(voice_app.state.db, "plant1", 120) is None
    device.post("/api/telemetry", json={**TELEMETRY, "soil_pct": 35})
    r = say(voice_app, device, "Джарвис, полей")
    assert unquote(r.headers["X-Answer"]) == "Поливаю, 3 с."
    assert store.pending_command(voice_app.state.db, "plant1", 120)["note"] == "voice"


def test_voice_limits(voice_app, device):
    voice_app.state.stt = FakeSTT("")
    assert device.post("/api/voice", content=b"\0" * (RATE * 2 * 9)).status_code == 413
    assert device.post("/api/voice", content=b"\0" * 100).status_code == 400
    assert device.post("/api/voice", content=PHRASE).status_code == 200


def test_voice_requires_token(voice_app, anon):
    assert anon.post("/api/voice", content=PHRASE).status_code == 401


def test_browser_voice_returns_json_and_log(voice_app, device, web):
    device.post("/api/telemetry", json=TELEMETRY)
    voice_app.state.stt = FakeSTT("как дела у цветка")
    r = web.post("/web/api/voice?device=plant1", content=b"\x1aE\xdf\xa3" + b"\0" * 2000)
    body = r.json()
    assert body["intent"] == "status" and body["transcript"] == "как дела у цветка"
    log = web.get(f"/web/api/voice/log?after={body['id'] - 1}").json()
    assert log["enabled"] and [i["id"] for i in log["items"]] == [body["id"]]
    assert web.get("/web/api/voice/log?after=" + str(body["id"])).json()["items"] == []


def test_voice_accepts_chunked_upload_like_device(voice_app, device):
    """Устройство шлёт фразу потоком (Transfer-Encoding: chunked), не храня её целиком (V2)."""
    voice_app.state.stt = FakeSTT("как дела")

    def chunks():
        for i in range(0, len(PHRASE), 320):
            yield PHRASE[i:i + 320]
    r = device.post("/api/voice", content=chunks(),
                    headers={"Content-Type": "audio/L16; rate=16000; channels=1"})
    assert r.status_code == 200
    audio = voice_app.state.stt.seen[0]
    assert len(audio) == RATE                                  # все 16000 отсчётов дошли


def test_demo_mode_shortens_only_interval(app, device):
    """D1: явный флаг — интервал 2 мин вместо 30, остальные лимиты прежние; DEMO в ответе."""
    db = app.state.db
    device.post("/api/telemetry", json={**TELEMETRY, "soil_pct": 40})
    store.save_event(db, "plant1", "water", 3, "voice")
    with db.conn() as c:
        c.execute("UPDATE events SET ts = ts - 300")                 # 5 мин назад
    assert not safety.check(db, "plant1", 3).ok
    safety.demo_mode = True
    try:
        assert safety.check(db, "plant1", 3).ok
        assert safety.check(db, "plant1", 30).seconds == 10        # 10 с — как всегда
        app.state.settings = app.state.settings.__class__(
            **{**app.state.settings.__dict__, "demo_mode": True})
        assert '"demo":1' in device.post("/api/telemetry", json={**TELEMETRY,
                                                                 "soil_pct": 75}).text
        assert not safety.check(db, "plant1", 3).ok                 # > 70 % — как всегда
    finally:
        safety.demo_mode = False
