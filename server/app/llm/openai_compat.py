"""OpenAI-совместимый API: DeepSeek, YandexGPT и др. (base_url, api_key, model)."""

import httpx

from ..config import Settings
from . import LLMError, Message


class OpenAICompatible:
    name = "openai"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.url = settings.openai_base_url.rstrip("/") + "/chat/completions"
        self.model = settings.openai_model
        self.http = httpx.Client(
            timeout=settings.llm_timeout_s, transport=transport,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )

    def chat(self, messages: list[Message]) -> str:
        try:
            r = self.http.post(self.url, json={"model": self.model, "messages": messages})
        except httpx.HTTPError as e:
            raise LLMError(f"LLM недоступна: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"LLM: ошибка {r.status_code}")
        return r.json()["choices"][0]["message"]["content"].strip()
