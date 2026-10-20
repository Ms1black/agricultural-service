"""Проверка ключа LLM: python -m app.llm "вопрос".

В Docker: docker compose exec plant python -m app.llm
"""

import sys

from ..config import Settings
from . import LLMError, make_provider


def main() -> int:
    settings = Settings.from_env()
    llm = make_provider(settings)
    if llm is None:
        print(f"LLM не настроена: LLM_PROVIDER={settings.llm_provider}, ключ не задан")
        return 1
    question = " ".join(sys.argv[1:]) or "Привет! Ответь одним предложением."
    try:
        print(f"[{llm.name}] {llm.chat([{'role': 'user', 'content': question}])}")
    except LLMError as e:
        print(f"[{llm.name}] ошибка: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
