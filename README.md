# Автополив с ИИ

Программно-аппаратный комплекс ухода за комнатным растением: устройство на ESP32-S3 меряет влажность
почвы и воздуха, поливает и слушает голосового помощника «Джарвиса»; сервис прогнозирует, когда
понадобится полив, подстраивается под конкретный горшок и показывает всё на сайте.

- **Устройство** (`firmware/`) — ESP32-S3, датчик почвы, AHT20, микрофон MAX9814, реле насоса,
  дисплей ST7735S. Поливает по порогу само, даже без связи с сервером.
- **Бэкенд** (`server/`) — FastAPI + SQLite: приём телеметрии, прогноз высыхания (scikit-learn),
  калибровка под растение, решения о поливе со слоем безопасности, голос (faster-whisper + Silero),
  чат с ИИ (GigaChat или OpenAI-совместимый API).
- **Фронтенд** (`frontend/`) — React + TypeScript + Vite, отдаётся nginx вместе с прокси к бэкенду.

```
устройство ──HTTP──▶ :8000 nginx ──▶ /api/*      ──▶ бэкенд (FastAPI)
браузер    ──HTTP──▶ :8000 nginx ──▶ /, /web/api ──▶ бэкенд (пароль проверяет он)
```

Подробный план, контракты API и лимиты безопасности — [PLAN.md](PLAN.md).

---

## Быстрый старт (Docker)

Нужно: Docker с плагином compose, **≥ 4 ГБ RAM** (распознавание и синтез речи), ~3 ГБ диска.

```bash
git clone https://github.com/Ms1black/agricultural-service.git
cd agricultural-service
cp server/.env.example server/.env
```

Заполните `server/.env` (минимум — две строки):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # → PLANT_TOKEN
```

| Переменная | Что это |
|---|---|
| `PLANT_TOKEN` | токен устройства; его же вводите в форме настройки устройства |
| `PLANT_WEB_PASSWORD` | пароль сайта (логин — `PLANT_WEB_USER`, по умолчанию `admin`) |
| `GIGACHAT_AUTH_KEY` | ключ GigaChat для чата и голосовых ответов ([developers.sber.ru](https://developers.sber.ru/studio)); без него ИИ отвечает шаблоном по данным |
| `VOICE_ENABLED`, `VOICE_PRELOAD` | голос и загрузка его моделей при старте (`1`) |
| `DEMO_MODE` | демо для записи видео: интервал поливов сервера 2 мин вместо 30, метка DEMO |

Запуск:

```bash
docker compose up -d --build
docker compose ps                 # plant — healthy, web — running
```

Откройте `http://<IP>:8000/` — браузер спросит логин и пароль. Первый запуск качает модели голоса
(~0.5 ГБ, несколько минут): `docker compose logs -f plant` → «модели голоса загружены».

### Модель прогноза

Своих данных на старте нет, поэтому модель учится на открытых датасетах ([docs/datasets.md](docs/datasets.md)):

```bash
docker compose exec plant python -m app.ml.datasets download   # открытые данные → том /data
docker compose exec plant python -m app.ml train                # 3–6 мин; в конце "champion": true
```

Дальше сервис переобучается сам раз в сутки (`ML_RETRAIN_HOURS`) и подключает данные своего растения,
как только у него наберётся 3 цикла «полив → высыхание». Отчёт с метриками — [docs/ml_report.md](docs/ml_report.md).

### Обновление

```bash
git pull
docker compose up -d --build
```

База, модели и записи голоса — в томе `server_plant-data`, переживают пересборку. Резервная копия:

```bash
docker compose cp plant:/data/plant.db ./plant-backup.db
```

> Переезд со старой схемы (сервис запускался из `server/`): `docker compose -p server down`,
> затем из корня `docker compose up -d --build` — том тот же, данные на месте.

---

## Устройство

Нужно: [PlatformIO](https://platformio.org/install/cli) (`pip install platformio`).

```bash
cd firmware
pio run -t upload        # прошить
pio device monitor       # монитор порта, 115200
```

**Настройка с телефона** — без перепрошивки:

1. Без сохранённой сети устройство само включает точку доступа; вручную — **3 хлопка**.
   На экране — `Setup: 192.168.4.1`.
2. Подключитесь к Wi-Fi `Autopoliv-Setup` (пароль `plant1234`), откройте `http://192.168.4.1`.
3. Выберите сеть, введите её пароль, адрес сервера `http://<IP>:8000` и `PLANT_TOKEN` → «Сохранить».
4. После перезагрузки на экране `WiFi <ip>`, на сайте пошли показания.

**Что умеет:**

- поливает само, когда почва суше 30 % (или по прогнозу ИИ — режим на сайте, вкладка «Полив»);
- «Джарвис, как там мой цветок?» — голосовой ответ (звучит из браузера, если на сайте включён звук);
- 3 хлопка — режим настройки (сменить Wi-Fi, например на раздачу с телефона);
- без сервера продолжает поливать по порогу; без связи копит замеры и досылает их.

Распиновка, ограничения и порядок проверки на железе — [PLAN.md](PLAN.md) (раздел 2)
и [docs/checklist.md](docs/checklist.md). Звук на самом устройстве (PAM8403 / MAX98357A) — `AUDIO_OUT`
в `firmware/autopoliv_ui/audio_out.h` (выводы сначала согласовать).

---

## Сайт

| Вкладка | Что там |
|---|---|
| **Обзор** | влажность почвы, когда полив, Джарвис, температура и воздух, график с прогнозом |
| **Джарвис** | чат с ИИ и голосовые разговоры — отдельный чат на каждый час |
| **ИИ-прогноз** | точность модели против простых способов, подстройка под растение, порог против ИИ |
| **Полив** | режим (порог / ИИ), полить сейчас, калибровка под растение, решения сервера |
| **Журнал** | события и выгрузка CSV |

Имя помощника меняется по клику на имя в шапке — на новое имя он откликается голосом.

Полезные адреса: `/#model` и др. — сразу нужная вкладка; `/?present=1` — крупно, без кнопок
(запись экрана); `/?present=1&source=demo:soil_monitor` — открытый датасет вместо устройства.

---

## Разработка

**Бэкенд** (Python 3.12):

```bash
cd server
pip install -r requirements-dev.txt
PLANT_INSECURE=1 PLANT_DB=./data/plant.db uvicorn app.main:create_app --factory --reload
pytest && ruff check .
```

`PLANT_INSECURE=1` — без токена и пароля, только для локальной работы. Голос локально — дополнительно
`pip install --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 && pip install -r requirements-voice.txt`.

**Фронтенд** (Node 22):

```bash
cd frontend
npm ci
npm run dev              # http://localhost:5173, API проксируется на http://localhost:8000
npm run build            # проверка типов + сборка в dist/
```

Другой адрес бэкенда для разработки: `BACKEND_URL=http://127.0.0.1:8001 npm run dev`.

**Прошивка:** `cd firmware && pio run`. Варианты, которые проверяет CI:
`PLATFORMIO_BUILD_FLAGS="-DAUDIO_OUT=AUDIO_PDM" pio run` (а также `AUDIO_I2S_MAX98357`,
`-DVOICE_ENABLED=0`, `-DWAKE_ENABLED=0`).

**Проверки без устройства:**

```bash
cd server
python scripts/voice_test.py phrase.wav --server http://<IP>:8000 --token <PLANT_TOKEN>   # голос
python scripts/voice_eval.py --server http://<IP>:8000 --token <PLANT_TOKEN>               # метрики голоса
```

CI (GitHub Actions) на каждый push: сборка прошивки во всех вариантах, `ruff` + `pytest`,
сборка фронтенда, `docker compose` — оба контейнера поднимаются, вход по паролю и API устройства работают.

---

## Структура

```
docker-compose.yml        бэкенд + фронтенд
server/                   FastAPI: app/{api,web,ml,voice,llm,…}, tests/, scripts/
frontend/                 React + Vite: src/, nginx.conf.template, Dockerfile
firmware/                 PlatformIO: autopoliv_ui/ (прошивка, спрайт Джарвиса, вывод звука)
docs/                     развёртывание, датасеты, отчёты, демо, чеклист проверки
PLAN.md                   план, контракты, лимиты безопасности
```

---

## Если что-то не так

| Симптом | Что проверить |
|---|---|
| `plant` не становится healthy | `docker compose logs plant` — обычно не заданы `PLANT_TOKEN` / `PLANT_WEB_PASSWORD` |
| Сайт не спрашивает пароль / 502 | `docker compose ps` — `plant` должен быть healthy, затем `docker compose restart web` |
| Устройство не шлёт данные | монитор порта: `ошибка 401` — неверный токен, `-1` — неверный адрес или сервер недоступен |
| Чат: «ИИ недоступен» | `GIGACHAT_AUTH_KEY` в `server/.env`, затем `docker compose up -d`; проверка: `docker compose exec plant python -m app.llm "Привет"` |
| Голос не отвечает | `curl http://<IP>:8000/health` → `"voice":true`; первый старт качает модели — подождите |
| Нет прогноза на сайте | обучите модель (см. выше); без модели — простая экстраполяция |
| Форма настройки на телефоне не открывается | выключите сотовые данные и VPN, наберите `http://192.168.4.1` полностью |

Секреты (`server/.env`, `firmware/autopoliv_ui/secrets.h`) в git не попадают — не коммитьте их.
