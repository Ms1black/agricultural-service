"""Аудио: PCM 16 кГц ↔ numpy, WAV, текст для синтеза речи."""

import io
import re
import wave

import numpy as np

RATE = 16000
MAX_PHRASE_S = 8.5        # фраза ≤ 8 с (контракт 4.3) + запас на тишину в конце
MAX_ANSWER_S = 20


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.float32) / 32768


def float_to_pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def wav_bytes(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def read_wav(data: bytes) -> np.ndarray:
    """WAV (16 бит, моно, 16 кГц) → float; для тестового скрипта без устройства."""
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != RATE:
            raise ValueError("нужен WAV 16 бит, моно, 16 кГц")
        return pcm16_to_float(w.readframes(w.getnframes()))


def resample(x: np.ndarray, src: int, dst: int = RATE) -> np.ndarray:
    if src == dst:
        return x
    from math import gcd

    from scipy.signal import resample_poly
    g = gcd(src, dst)
    return resample_poly(x, dst // g, src // g).astype(np.float32)


UNITS = [(r"\s*%", " процентов"), (r"\s*°\s*[CС]", " градусов"), (r"\s*°", " градусов"),
         (r"\s*ч\b", " часов"), (r"\s*мин\b", " минут"), (r"\s*с\b(?=[\s.,!?]|$)", " секунд")]


def speech_text(text: str, limit: int = 400) -> str:
    """Текст для синтеза: без разметки и латиницы, числа словами, не длиннее limit символов."""
    from num2words import num2words
    t = re.sub(r"[*_#`>\[\]()«»\"]", " ", text)
    t = t.replace("≈", " примерно ").replace("~", " примерно ")
    t = re.sub(r"\s[—–-]\s", ", ", t)
    t = re.sub(r"https?://\S+", " ", t)
    for pattern, word in UNITS:
        t = re.sub(r"(?<=\d)" + pattern, word, t)

    def number(m: re.Match) -> str:
        value = round(float(m.group(0).replace(",", ".")))
        return num2words(value, lang="ru")
    t = re.sub(r"\d+(?:[.,]\d+)?", number, t)
    t = re.sub(r"[A-Za-z]+", " ", t)
    t = re.sub(r"[^\w\s.,!?:;-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        cut = t[:limit]
        t = cut[: max(cut.rfind("."), cut.rfind("!"), cut.rfind("?")) + 1] or cut
    return t
