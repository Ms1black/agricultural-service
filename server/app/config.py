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
    # LLM (задача L1): gigachat | openai | none. Без ключа LLM выключена.
    llm_provider: str = "gigachat"
    llm_timeout_s: float = 30
    gigachat_auth_key: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat"
    # Сертификат НУЦ Минцифры (в Docker-образе уже есть)
    gigachat_ca_bundle: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    openai_api_key: str = ""
    openai_model: str = "deepseek-chat"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.environ.get("PLANT_DB", cls.db_path),
            token=os.environ.get("PLANT_TOKEN", ""),
            web_user=os.environ.get("PLANT_WEB_USER", cls.web_user),
            web_password=os.environ.get("PLANT_WEB_PASSWORD", ""),
            insecure=_flag("PLANT_INSECURE"),
            llm_provider=os.environ.get("LLM_PROVIDER", cls.llm_provider).strip().lower(),
            llm_timeout_s=float(os.environ.get("LLM_TIMEOUT_S", cls.llm_timeout_s)),
            gigachat_auth_key=os.environ.get("GIGACHAT_AUTH_KEY", "").strip(),
            gigachat_scope=os.environ.get("GIGACHAT_SCOPE", cls.gigachat_scope),
            gigachat_model=os.environ.get("GIGACHAT_MODEL", cls.gigachat_model),
            gigachat_ca_bundle=os.environ.get("GIGACHAT_CA_BUNDLE", ""),
            openai_base_url=os.environ.get("OPENAI_BASE_URL", cls.openai_base_url),
            openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            openai_model=os.environ.get("OPENAI_MODEL", cls.openai_model),
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
