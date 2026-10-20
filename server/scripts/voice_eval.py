"""Метрики голоса (6.5) на тестовом наборе: задержка, точность намерения, WER.

Набор — docs/voice_test/: WAV-файлы (16 бит, моно, 16 кГц) и phrases.csv со строками
    file,text,intent           (intent: water | status | chat)
Запуск (сервис должен работать):
    python scripts/voice_eval.py --server http://<IP>:8000 --token <PLANT_TOKEN>
Результат — docs/voice_test/results.md. Внимание: фразы с «полей» действительно просят полив
(через слой безопасности) — запускайте при влажной почве или без устройства.
"""

import argparse
import csv
import statistics
import sys
import time
import wave
from pathlib import Path
from urllib.parse import unquote

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.voice import intent  # noqa: E402

SET = Path(__file__).resolve().parents[2] / "docs" / "voice_test"


def words(text: str) -> list[str]:
    import re
    return re.findall(r"\w+", text.lower().replace("ё", "е"))


def wer(ref: str, hyp: str) -> float:
    """Доля ошибок по словам (расстояние Левенштейна / число слов эталона)."""
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--token", default="")
    p.add_argument("--set", type=Path, default=SET)
    a = p.parse_args()
    rows = list(csv.DictReader(open(a.set / "phrases.csv", encoding="utf-8")))
    if len(rows) < 30:
        print(f"Внимание: в наборе {len(rows)} фраз, по плану нужно ≥ 30")
    results = []
    for row in rows:
        with wave.open(str(a.set / row["file"])) as w:
            pcm = w.readframes(w.getnframes())
        t0 = time.monotonic()
        r = httpx.post(a.server.rstrip("/") + "/api/voice", content=pcm, timeout=60, headers={
            "Content-Type": "audio/L16; rate=16000; channels=1", "X-Api-Key": a.token,
            "X-Device": "voice-test"})
        dt = time.monotonic() - t0
        if r.status_code != 200:
            print(f"{row['file']}: ошибка {r.status_code}")
            continue
        hyp = unquote(r.headers["X-Transcript"])
        got = intent(hyp)
        results.append({"file": row["file"], "latency": dt, "wer": wer(row["text"], hyp),
                        "ok": got == row["intent"], "hyp": hyp, "intent": got,
                        "expected": row["intent"]})
        print(f"{row['file']}: {dt:.1f} с, {got}/{row['intent']}, «{hyp}»")
    if not results:
        return 1
    lat = sorted(x["latency"] for x in results)
    p90 = lat[min(len(lat) - 1, int(round(0.9 * (len(lat) - 1))))]
    acc = sum(x["ok"] for x in results) / len(results)
    mean_wer = statistics.mean(x["wer"] for x in results)
    lines = ["# Голосовой помощник: метрики (6.5)", "",
             f"Фраз: {len(results)}. Сервер: {a.server}.", "",
             "| Метрика | Значение | Цель |", "|---|---|---|",
             f"| Задержка ответа, медиана | {statistics.median(lat):.1f} с | ≤ 6 с |",
             f"| Задержка ответа, p90 | {p90:.1f} с | — |",
             f"| Точность намерения | {acc:.0%} | ≥ 90 % |",
             f"| WER распознавания (справочно) | {mean_wer:.0%} | — |", "",
             "Задержка измерена клиентом: от отправки фразы до получения ответа целиком.", "",
             "| Файл | Ожидалось | Получено | Распознано | WER | с |", "|---|---|---|---|---|---|"]
    lines += [f"| {x['file']} | {x['expected']} | {x['intent']} | {x['hyp']} | {x['wer']:.0%} "
              f"| {x['latency']:.1f} |" for x in results]
    (a.set / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Медиана {statistics.median(lat):.1f} с, p90 {p90:.1f} с, намерения {acc:.0%}, "
          f"WER {mean_wer:.0%} → {a.set / 'results.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
