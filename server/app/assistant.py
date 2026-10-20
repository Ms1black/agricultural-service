"""Имя голосового помощника (по умолчанию «Джарвис»): им он представляется и на него откликается."""

import re
import time

from .db import Database

DEFAULT_NAME = "Джарвис"
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё -]{1,19}$")
# Как Whisper слышит «Джарвис»: склейки с «хэй» и потерянное начало
DEFAULT_VARIANTS = r"джарвис|джервис|jarvis|жарвис|дарвис|чарвис|\w{0,2}арвис"
WAKE_PREFIX = r"^\W*(?:(?:эй|хэй|хей|хе|хай|hey|ай)\W*)?"


def get_name(db: Database) -> str:
    with db.conn() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key = 'assistant_name'").fetchone()
    return row["value"] if row else DEFAULT_NAME


def set_name(db: Database, name: str) -> str:
    name = " ".join(name.split())
    if not NAME_RE.match(name):
        raise ValueError("имя: 2–20 букв (можно пробел и дефис)")
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO app_settings (key, value, updated) "
                  "VALUES ('assistant_name', ?, ?)", (name, time.time()))
    return name


def wake_pattern(name: str) -> re.Pattern:
    """Обращение в начале фразы: имя (с «эй/хэй» или без); Whisper иногда теряет первую букву."""
    low = name.lower().replace("ё", "е")
    if low == DEFAULT_NAME.lower():
        core = DEFAULT_VARIANTS
    else:
        variants = [re.escape(low)]
        if len(low) >= 5:
            variants.append(r"\w?" + re.escape(low[1:]))     # «…ася» вместо «Вася»
        core = "|".join(variants)
    return re.compile(WAKE_PREFIX + rf"(?:{core})\b\W*", re.I)
