# Развёртывание сервиса на облачном сервере

Нужно: Linux, Docker с плагином compose, ≥ 4 ГБ RAM (под голос в будущем), открытый порт 8000.

```bash
git clone https://github.com/Ms1black/agricultural-service.git
cd agricultural-service/server
cp .env.example .env
# Сгенерировать токен устройства и пароль веба:
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
nano .env          # PLANT_TOKEN, PLANT_WEB_PASSWORD
docker compose up -d --build
docker compose ps  # статус должен стать healthy
curl http://localhost:8000/health
```

Без `PLANT_TOKEN` и `PLANT_WEB_PASSWORD` сервис не стартует (в логах будет причина:
`docker compose logs`). Для локального запуска без секретов — `PLANT_INSECURE=1`.

## Устройство

В `firmware/autopoliv_ui/secrets.h` (копия `secrets.example.h`):

```cpp
const char* SERVER    = "http://<IP-сервера>:8000";
const char* API_TOKEN = "<тот же PLANT_TOKEN>";
```

## Веб

`http://<IP-сервера>:8000/` — логин `PLANT_WEB_USER` (по умолчанию `admin`) и `PLANT_WEB_PASSWORD`.
Соединение без HTTPS: пароль и токен не использовать в других местах.

## Данные

База — в томе `plant-data` (`/data/plant.db` в контейнере), переживает пересборку.
Резервная копия:

```bash
docker compose cp plant:/data/plant.db ./plant-backup.db
```

Выгрузка CSV — ссылки внизу веб-страницы.

## Обновление

```bash
git pull && docker compose up -d --build
```

Миграции схемы применяются автоматически при старте.

## Локально без Docker

```bash
cd server
pip install -r requirements-dev.txt
PLANT_INSECURE=1 PLANT_DB=./data/plant.db uvicorn app.main:create_app --factory --reload
pytest && ruff check .
```
