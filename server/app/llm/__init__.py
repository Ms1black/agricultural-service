"""LLM-слой (задача L1): провайдер с интерфейсом chat(messages) -> text.

Провайдер выбирается настройкой LLM_PROVIDER. Без LLM сервис работает: чат отвечает «ИИ недоступен».
LLM только отвечает текстом — управлять поливом он не может (раздел 3 PLAN.md, принцип 2).
"""

from typing import Protocol

from ..config import Settings

Message = dict[str, str]   # {"role": "system" | "user" | "assistant", "content": "..."}


class LLMError(RuntimeError):
    """Провайдер не настроен или не ответил."""


class Provider(Protocol):
    name: str

    def chat(self, messages: list[Message]) -> str: ...


def make_provider(settings: Settings) -> Provider | None:
    if settings.llm_provider == "gigachat" and settings.gigachat_auth_key:
        from .gigachat import GigaChat
        return GigaChat(settings)
    if settings.llm_provider == "openai" and settings.openai_api_key:
        from .openai_compat import OpenAICompatible
        return OpenAICompatible(settings)
    return None
