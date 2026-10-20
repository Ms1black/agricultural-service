"""Контекст для модели: растение, текущие показания, последние поливы и их причины."""

import time

from .. import store
from ..db import Database
from . import Message

# Причины полива из контракта 4.2
WATER_REASONS = {
    "auto": "автополив по порогу 30 % (датчик A0)",
    "auto-D0": "автополив по цифровому датчику D0 (аналоговый недоступен)",
    "claps": "команда двумя хлопками",
    "web": "кнопка «Полить» на веб-странице",
    "manual": "вручную через монитор порта",
    "voice": "голосовая команда",
    "ml": "решение по прогнозу модели",
}
MAX_HISTORY = 10          # реплик чата, передаваемых модели
MAX_MESSAGE_CHARS = 2000

SYSTEM = """Ты — Джарвис, помощник по уходу за комнатным растением. Отвечай по-русски, коротко
(2–4 предложения), дружелюбно и только на основе данных ниже. Если данных не хватает — так и скажи,
не выдумывай. Сам ты поливать не можешь: поливает устройство по порогу или сервер по прогнозу,
вручную — кнопкой на веб-странице или голосом; любой полив проходит проверки безопасности
(не дольше 10 с, не чаще раза в 30 мин, не поливать, если почва влажнее 70 %)."""


def ago(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 1:
        return "только что"
    if m < 60:
        return f"{m} мин назад"
    if m < 48 * 60:
        return f"{m // 60} ч назад"
    return f"{m // 1440} дн назад"


def soil_trend(rows: list[dict]) -> float | None:
    """Скорость изменения влажности, %/ч (по крайним точкам; None — мало данных)."""
    rows = [r for r in rows if r["soil_ok"] and r["soil_pct"] is not None]
    if len(rows) < 2 or rows[-1]["ts"] - rows[0]["ts"] < 1800:
        return None
    return (rows[-1]["soil_pct"] - rows[0]["soil_pct"]) / ((rows[-1]["ts"] - rows[0]["ts"]) / 3600)


def describe(db: Database, device: str, now: float | None = None) -> str:
    now = now or time.time()
    lines = [f"Устройство: {device}."]
    m = store.latest_measurement(db, device)
    if not m:
        lines.append("Показаний с датчиков пока нет.")
    else:
        stale = " (данные устарели — устройство не на связи)" if now - m["ts"] > 120 else ""
        if m["soil_ok"]:
            soil = f"{m['soil_pct']} %"
        else:
            soil = ("сухо" if m["soil_dry_d0"] else "влажно") + " по цифровому датчику"
        lines.append(f"Последние показания {ago(now - m['ts'])}{stale}: влажность почвы {soil}, "
                     f"температура {_num(m['temp'], ' °C')}, "
                     f"влажность воздуха {_num(m['air_hum'], ' %')}.")
        trend = soil_trend(store.measurements_since(db, device, now - 6 * 3600))
        if trend is not None:
            lines.append(f"За последние часы влажность почвы меняется на {trend:+.1f} %/ч.")
    lines.append("Порог автополива — 30 %, целевой диапазон — 30–70 %.")
    waterings = [e for e in store.recent_events(db, device, limit=50) if e["type"] == "water"][:5]
    if waterings:
        lines.append("Последние поливы (новые сверху):")
        for e in waterings:
            reason = WATER_REASONS.get(e["note"] or "", e["note"] or "причина не указана")
            lines.append(f"- {ago(now - e['ts'])}, {_num(e['value'], ' с')}: {reason}")
    else:
        lines.append("Поливов в журнале пока нет.")
    lines.append(forecast_line(db, device))
    lines.extend(decision_lines(db, device, now))
    return "\n".join(lines)


def decision_lines(db: Database, device: str, now: float) -> list[str]:
    from .. import decision
    st = decision.status(db, device)
    out = [f"Режим полива: {st['active']}" + (
        f" (выбран ml, но {st['ml_reason']})" if st["mode"] == "ml" and not st["ml_available"]
        else "") + "."]
    recent = decision.recent(db, device, 5)
    if recent:
        out.append("Последние решения сервера (новые сверху):")
        out += [f"- {ago(now - d['ts'])}: {d['action']} — {d['reason']}" for d in recent]
    return out


def forecast_line(db: Database, device: str) -> str:
    try:
        from ..ml.forecast import describe as describe_forecast
        from ..ml.forecast import forecast
        return describe_forecast(forecast(db, device))
    except Exception:  # прогноз не должен ломать чат
        return "Прогноз сейчас недоступен — используется полив по порогу."


def build_messages(db: Database, device: str, history: list[Message]) -> list[Message]:
    """Системная подсказка с данными + последние реплики пользователя и ассистента."""
    clean = [{"role": h["role"], "content": h["content"][:MAX_MESSAGE_CHARS]}
             for h in history if h.get("role") in ("user", "assistant") and h.get("content")]
    return [{"role": "system", "content": SYSTEM + "\n\nДанные:\n" + describe(db, device)},
            *clean[-MAX_HISTORY:]]


def _num(v: float | None, unit: str) -> str:
    return "нет данных" if v is None else f"{round(v, 1):g}{unit}"
