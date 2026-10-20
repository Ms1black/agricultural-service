"""Проверка голоса без устройства: WAV-файл → /api/voice → ответ в answer.wav.

    python scripts/voice_test.py phrase.wav --server http://<IP>:8000 --token <PLANT_TOKEN>

Нужен WAV 16 бит, моно, 16 кГц (например: ffmpeg -i in.m4a -ac 1 -ar 16000 phrase.wav).
"""

import argparse
import io
import sys
import time
import wave
from pathlib import Path
from urllib.parse import unquote

import httpx


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("wav", type=Path)
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--token", default="")
    p.add_argument("--device", default="plant1")
    p.add_argument("--out", type=Path, default=Path("answer.wav"))
    a = p.parse_args()
    with wave.open(str(a.wav)) as w:
        if (w.getsampwidth(), w.getnchannels(), w.getframerate()) != (2, 1, 16000):
            print("Нужен WAV 16 бит, моно, 16 кГц")
            return 1
        pcm = w.readframes(w.getnframes())
    t0 = time.monotonic()
    r = httpx.post(a.server.rstrip("/") + "/api/voice", content=pcm, timeout=60, headers={
        "Content-Type": "audio/L16; rate=16000; channels=1", "X-Device": a.device,
        "X-Api-Key": a.token})
    dt = time.monotonic() - t0
    if r.status_code != 200:
        print(f"Ошибка {r.status_code}: {r.text}")
        return 1
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(r.content)
    a.out.write_bytes(buf.getvalue())
    print(f"Распознано: {unquote(r.headers['X-Transcript'])}")
    print(f"Ответ:      {unquote(r.headers['X-Answer'])}")
    print(f"Время: {dt:.1f} с, звук ответа {len(r.content) / 32000:.1f} с → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
