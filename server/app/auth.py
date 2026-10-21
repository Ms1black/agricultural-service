"""Авторизация: X-Api-Key для устройства, HTTP Basic для веба (раздел 4.4)."""

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials

from .config import Settings

_basic = HTTPBasic(auto_error=False)
# Схемы безопасности — ещё и для кнопки Authorize в Swagger (/web/api/docs)
_api_key = APIKeyHeader(name="X-Api-Key", auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _eq(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def require_device(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Depends(_api_key),
) -> None:
    if not settings.token:
        return
    if not x_api_key or not _eq(x_api_key, settings.token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad api key")


def require_web(
    settings: Settings = Depends(get_settings),
    cred: HTTPBasicCredentials | None = Depends(_basic),
) -> None:
    if not settings.web_password:
        return
    ok = (
        cred is not None
        and _eq(cred.username, settings.web_user)
        and _eq(cred.password, settings.web_password)
    )
    if not ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "auth required",
            headers={"WWW-Authenticate": 'Basic realm="plant"'},
        )
