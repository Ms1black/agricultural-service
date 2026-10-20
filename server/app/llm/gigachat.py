"""GigaChat: OAuth по ключу авторизации → access token (живёт 30 мин, обновляется сам)."""

import ssl
import threading
import time
import uuid
from pathlib import Path

import httpx

from ..config import Settings
from . import LLMError, Message

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
# Обновлять токен заранее, чтобы он не истёк посреди запроса
TOKEN_MARGIN_S = 60
# Сертификат НУЦ Минцифры в репозитории (server/certs), если GIGACHAT_CA_BUNDLE не задан
DEFAULT_CA = Path(__file__).resolve().parents[2] / "certs" / "russian_trusted_root_ca.pem"


def ssl_context(ca_bundle: str) -> ssl.SSLContext:
    """Системные корневые сертификаты + сертификат НУЦ Минцифры (им подписан GigaChat)."""
    ctx = ssl.create_default_context()
    ca_bundle = ca_bundle or (str(DEFAULT_CA) if DEFAULT_CA.exists() else "")
    if ca_bundle:
        ctx.load_verify_locations(cafile=ca_bundle)
    return ctx


class GigaChat:
    name = "gigachat"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.auth_key = settings.gigachat_auth_key
        self.scope = settings.gigachat_scope
        self.model = settings.gigachat_model
        verify = True if transport else ssl_context(settings.gigachat_ca_bundle)
        self.http = httpx.Client(timeout=settings.llm_timeout_s, verify=verify,
                                 transport=transport)
        self._token = ""
        self._expires = 0.0
        self._lock = threading.Lock()

    def _get_token(self, force: bool = False) -> str:
        with self._lock:
            if force or not self._token or time.time() > self._expires - TOKEN_MARGIN_S:
                r = self.http.post(
                    OAUTH_URL,
                    headers={"Authorization": f"Basic {self.auth_key}",
                             "RqUID": str(uuid.uuid4()), "Accept": "application/json"},
                    data={"scope": self.scope},
                )
                if r.status_code != 200:
                    raise LLMError(f"GigaChat: авторизация не прошла ({r.status_code})")
                data = r.json()
                self._token = data["access_token"]
                self._expires = data["expires_at"] / 1000   # в ответе — миллисекунды
            return self._token

    def chat(self, messages: list[Message]) -> str:
        body = {"model": self.model, "messages": messages}
        try:
            r = self._post_chat(body, self._get_token())
            if r.status_code == 401:            # токен отозван раньше срока — один повтор
                r = self._post_chat(body, self._get_token(force=True))
        except httpx.HTTPError as e:
            raise LLMError(f"GigaChat недоступен: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"GigaChat: ошибка {r.status_code}")
        return r.json()["choices"][0]["message"]["content"].strip()

    def _post_chat(self, body: dict, token: str) -> httpx.Response:
        return self.http.post(CHAT_URL, json=body, headers={"Authorization": f"Bearer {token}"})
