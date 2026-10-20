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

Сеть, адрес сервера и токен задаются с телефона — перепрошивка не нужна.

1. Прошить: `cd firmware && pio run -t upload` (монитор порта — `pio device monitor`).
2. Если сеть не сохранена или не подключилась за 10 с, устройство включает режим настройки:
   в шапке экрана — `Setup: 192.168.4.1`. Вручную — **3 хлопка** (ещё 3 — выход).
3. С телефона подключиться к Wi-Fi `Autopoliv-Setup`, пароль `plant1234`.
   Форма откроется сама, иначе — `http://192.168.4.1`.
4. Выбрать сеть из списка, ввести пароль Wi-Fi, адрес сервера `http://<IP-сервера>:8000`
   и токен (тот же `PLANT_TOKEN`) → «Сохранить и перезагрузить».
5. После перезагрузки на экране `WiFi <ip>`, на веб-странице каждые 30 с — новые показания.

Настройки хранятся в памяти устройства и переживают выключение. Пустой пароль Wi-Fi или токен
в форме — «не менять». Если роутер был недоступен, устройство в режиме настройки без подключённых
клиентов раз в 5 мин снова пробует подключиться. Стереть настройки — `pio run -t erase` и прошить заново.

Сменить сеть (например, на раздачу с телефона на выставке) — 3 хлопка и та же форма.

`secrets.h` (копия `secrets.example.h`) необязателен: его значения используются, только пока
в памяти устройства ничего не сохранено.

## ИИ: чат и «Почему полили?» (GigaChat)

1. Ключ: [developers.sber.ru](https://developers.sber.ru/studio) → проект GigaChat API (Freemium для
   физлиц) → «Ключ авторизации». Он уже в base64 — копировать как есть.
2. В `server/.env`: `LLM_PROVIDER=gigachat`, `GIGACHAT_AUTH_KEY=<ключ>`. Ключ не коммитить и не пересылать.
3. `git pull && docker compose up -d --build` — пересборка с новым кодом и `.env`.
4. Проверка: `docker compose exec plant python -m app.llm "Привет"` → ответ модели;
   `curl http://localhost:8000/health` → `"llm":"gigachat"`.

Сертификат НУЦ Минцифры (им подписан GigaChat) лежит в `server/certs/` и уже есть в образе.
Без ключа сервис работает, чат показывает «ИИ недоступен». Другой провайдер (DeepSeek, YandexGPT) —
`LLM_PROVIDER=openai` и переменные `OPENAI_*` из `.env.example`.

## ML: прогноз высыхания

Модель обучается на открытых датасетах ([datasets.md](datasets.md)), результаты — [ml_report.md](ml_report.md).

```bash
docker compose exec plant python -m app.ml.datasets download   # открытые данные → /data/external
docker compose exec plant python -m app.ml train                # обучение, сравнение, реестр
```

Модель становится рабочей, только если лучше бейзлайнов и текущей рабочей (раздел 6.6 плана).
Прогноз: `GET /web/api/forecast?device=plant1` (веб) и `GET /api/forecast` (устройство, токен).
На своих циклах прогноз адаптируется автоматически. Без модели — линейная экстраполяция.

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
