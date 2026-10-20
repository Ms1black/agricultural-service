"""Голосовой помощник (V1): фраза → STT → намерение → ответ (LLM или шаблон) → TTS → журнал.

«Джарвис, полей» — только запрос: он проходит слой безопасности (app.safety), как и любой полив.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import assistant, safety, store
from ..db import Database
from ..llm import LLMError
from ..llm.context import build_messages
from . import engines  # noqa: F401  (VoiceUnavailable для вызывающих)
from .audio import MAX_ANSWER_S, RATE, float_to_pcm16, speech_text, wav_bytes

VOICE_WATER_SECONDS = 3

WAKE = re.compile(r"^\W*(?:эй\W+|hey\W+)?(?:джарвис|jarvis|жарвис|дарвис)\W*", re.I)
WATER = re.compile(r"\b(пол[еи]й|полить|поливай|полейте|налей\w*|напои\w*|дай\w* (?:попить|воды))",
                   re.I)
STATUS = re.compile(r"(как\s+(?:там|дела|себя|поживает|он|она|оно|мой|моя|цвет)|что\s+с\s+"
                    r"|состояни|расскажи|влажн|сух|пора\s+поли|когда\s+поли)", re.I)

# V3: обращение по имени помощника в начале фразы (по умолчанию «Джарвис», меняется в вебе)
WAKE_WORD = assistant.wake_pattern(assistant.DEFAULT_NAME)
LISTEN_ANSWER = "Слушаю."
# Подряд много фраз без обращения (телевизор, разговор) — устройство просим подождать
WAKE_MAX_MISSES = 6
WAKE_MISS_WINDOW_S = 120
WAKE_RETRY_S = 30
_misses: dict[str, list[float]] = {}

SPEECH_HINT = ("Ответ будет произнесён вслух: 1–3 коротких предложения, без списков, "
               "без смайликов и без разметки.")


@dataclass
class VoiceResult:
    id: int
    transcript: str
    answer: str
    intent: str
    pcm: bytes
    ms_stt: int
    ms_llm: int
    ms_tts: int
    listen: bool = False      # прозвучало только «Джарвис» — устройство слушает вопрос


def wake_rest(text: str, name: str = assistant.DEFAULT_NAME) -> str | None:
    """Текст после обращения по имени или None, если обращения в начале нет."""
    m = assistant.wake_pattern(name).match(text)
    return text[m.end():].strip() if m else None


def wake_busy(device: str, now: float | None = None) -> bool:
    now = now or time.time()
    recent = [t for t in _misses.get(device, []) if now - t < WAKE_MISS_WINDOW_S]
    _misses[device] = recent
    return len(recent) >= WAKE_MAX_MISSES


def wake_miss(device: str) -> None:
    _misses.setdefault(device, []).append(time.time())


def intent(text: str, name: str = assistant.DEFAULT_NAME) -> str:
    """water — просьба полить; status — вопрос о растении; chat — всё остальное."""
    t = assistant.wake_pattern(name).sub("", WAKE.sub("", text))
    if WATER.search(t) and not re.search(r"\bне\s+пол", t, re.I):
        return "water"
    if STATUS.search(t):
        return "status"
    return "chat" if t.strip() else "empty"


def status_template(db: Database, device: str) -> str:
    """Ответ без LLM — по данным устройства и прогнозу."""
    m = store.latest_measurement(db, device)
    if not m:
        return "Пока нет данных с датчиков. Проверьте, что устройство на связи."
    parts = []
    if m["soil_ok"] and m["soil_pct"] is not None:
        parts.append(f"Влажность почвы {m['soil_pct']} %.")
    else:
        parts.append("Почва сухая." if m["soil_dry_d0"] else "Почва влажная.")
    if m["temp"] is not None:
        parts.append(f"Температура {round(m['temp'])} °C.")
    try:
        from ..ml.forecast import forecast
        fc = forecast(db, device)
        if fc.get("status") == "ok":
            h = fc["hours_to_threshold"]
            parts.append("Пора поливать." if h <= 0 else
                         f"Следующий полив примерно через {round(h)} ч." if h < 48 else
                         f"Следующий полив примерно через {round(h / 24)} дней.")
    except Exception:
        pass
    return " ".join(parts)


def answer_for(db: Database, llm, device: str, text: str, kind: str) -> str:
    if kind == "empty":
        return "Не расслышал. Повторите, пожалуйста."
    if kind == "water":
        m = store.latest_measurement(db, device)
        seconds = None
        if m and m["soil_ok"] and m["soil_pct"] is not None:
            from ..calibration import dose_seconds
            seconds = dose_seconds(db, device, m["soil_pct"])
        v = safety.request_water(db, device, seconds or VOICE_WATER_SECONDS, "voice")
        return (f"Поливаю, {v.seconds} с." if v.ok else f"Не буду поливать: {v.reason}.")
    if llm is not None:
        try:
            messages = build_messages(db, device, [{"role": "user", "content": text}])
            messages[0]["content"] += "\n\n" + SPEECH_HINT
            return llm.chat(messages)
        except LLMError:
            pass
    if kind == "status":
        return status_template(db, device)
    return "Я умею рассказать о растении и полить его. Спросите: как там мой цветок?"


def handle(db: Database, llm, stt, tts, voice_dir: str, device: str, audio,
           wake: bool = False) -> VoiceResult | None:
    """audio — float32 16 кГц или файл, который декодирует STT.

    wake=True (V3, «Джарвис» без хлопка): фраза без обращения в начале — None, ничего не пишем;
    только «Джарвис» — ответ «Слушаю.» и listen=True; иначе — обычный ответ на остаток фразы.
    """
    name = assistant.get_name(db)
    if hasattr(stt, "assistant_name"):
        stt.assistant_name = name                 # подсказка распознаванию: имя помощника
    t0 = time.monotonic()
    transcript = stt.transcribe(audio)
    t1 = time.monotonic()
    listen = False
    if wake:
        rest = wake_rest(transcript, name)
        if rest is None:
            wake_miss(device)
            return None
        _misses.pop(device, None)
        listen = not rest
    if listen:
        kind, answer = "wake", LISTEN_ANSWER
    else:
        kind = intent(transcript, name)
        answer = answer_for(db, llm, device, transcript, kind)
    t2 = time.monotonic()
    speech = tts.synthesize(speech_text(answer)) if answer else np.zeros(0, np.float32)
    pcm = float_to_pcm16(speech[: MAX_ANSWER_S * RATE])
    t3 = time.monotonic()
    ms = [round((b - a) * 1000) for a, b in ((t0, t1), (t1, t2), (t2, t3))]
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO voice_log (ts, device, transcript, answer, intent, ms_stt, ms_llm, "
            "ms_tts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), device, transcript, answer, kind, *ms))
        vid = cur.lastrowid
    folder = Path(voice_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{vid}.wav"
    path.write_bytes(wav_bytes(pcm))
    with db.conn() as c:
        c.execute("UPDATE voice_log SET audio_path = ? WHERE id = ?", (str(path), vid))
    return VoiceResult(vid, transcript, answer, kind, pcm, *ms, listen=listen)


def log(db: Database, device: str | None = None, after: int = 0, limit: int = 30) -> list[dict]:
    q = ("SELECT id, ts, device, transcript, answer, intent, ms_stt, ms_llm, ms_tts FROM voice_log "
         "WHERE id > ?" + (" AND device = ?" if device else "") + " ORDER BY id DESC LIMIT ?")
    args = (after, device, limit) if device else (after, limit)
    with db.conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def audio_path(db: Database, vid: int) -> str | None:
    with db.conn() as c:
        row = c.execute("SELECT audio_path FROM voice_log WHERE id = ?", (vid,)).fetchone()
    return row["audio_path"] if row else None
