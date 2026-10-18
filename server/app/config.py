"""Настройки сервиса из переменных окружения (см. .env.example)."""

import os
from dataclasses import dataclass


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    db_path: str = "/data/plant.db"
    # Токен устройства (заголовок X-Api-Key). Пустой — проверка выключена (только при insecure).
    token: str = ""
    web_user: str = "admin"
    web_password: str = ""
    # Разрешить запуск без токена и пароля — только для локальной разработки.
    insecure: bool = False
    # Команда полива из веба ждёт устройство не дольше этого времени.
    command_ttl_s: int = 120

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.environ.get("PLANT_DB", cls.db_path),
            token=os.environ.get("PLANT_TOKEN", ""),
            web_user=os.environ.get("PLANT_WEB_USER", cls.web_user),
            web_password=os.environ.get("PLANT_WEB_PASSWORD", ""),
            insecure=_flag("PLANT_INSECURE"),
        )

    def check(self) -> None:
        """На облаке токен и пароль обязательны: без них сервис не стартует."""
        if self.insecure:
            return
        missing = [n for n, v in (("PLANT_TOKEN", self.token),
                                  ("PLANT_WEB_PASSWORD", self.web_password)) if not v]
        if missing:
            raise RuntimeError(
                f"Не заданы {', '.join(missing)}. Задайте их в .env "
                "или PLANT_INSECURE=1 для локального запуска."
            )
