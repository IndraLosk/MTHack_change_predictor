# MTHack_change_predictor

Backend-решение для хакатона Московского транспорта: приём потоковой телеметрии **NDTP**,
накопление в PostgreSQL и CSV, сопоставление с расписанием и **ML-прогноз задержки
в горизонте 10–15 минут** до события (LightGBM), с выдачей через API и общую БД.

---

## Что делает система

- Непрерывно принимает и парсит потоковые телематические пакеты **NDTP** (от эмулятора).
- Накопливает телеметрию в PostgreSQL (`ndtp_telemetry`) и CSV (запасной контур).
- Сопоставляет с расписанием и считает производные признаки: текущее отклонение от графика,
  среднюю скорость на сегменте, долю простоя, позицию на маршруте.
- Предсказывает задержку на следующей плановой остановке в окне **T+10…15 мин**
  (LightGBM по 24 признакам, только на данных, доступных на момент T — без утечки).
- Формирует **живое раннее оповещение** (live-алерты) на потоке и офлайн-прогнозы по
  валидационным точкам — всё пишется в таблицу `predictions`.
- Отдаёт результат через **HTTP API** (Swagger на `/docs`) и через общую БД.

---

## Архитектура и модули

Решение из **4 контейнеров** (Docker Compose), с чётким разделением
Backend (API/оркестрация) и ML-ядра (инференс):

| Сервис | Роль | Порт |
|---|---|---|
| `ndtp-emu` | Эмулятор телеметрии NDTP (шлёт пакеты по TCP) | 18080 (REST), TCP по конфигу |
| `db` | PostgreSQL 16 — единое хранилище телеметрии, расписания, прогнозов | 5433 |
| `backend` | FastAPI: TCP-приёмник NDTP :9201, парсинг, признаки, API | 8000 |
| `ml-model` | LightGBM-инференс: офлайн-точки + live-алерты, Map Matching | — |

Связи:
- `ndtp-emu` → **TCP :9201** → `backend` (приём и раскодировка пакетов);
- `backend` → `db` (персистентная запись телеметрии);
- `ml-model` → `db` (читает телеметрию и расписание, пишет прогнозы);
- `backend`, `db`, `ml-model` работают через общий PostgreSQL;
- наружу (`backend`) данные и прогнозы отдаются по HTTP: `/api/predictions`, `/api/ndtp/...`.

Строка подключения к БД: `postgresql://mthack:mthack@localhost:5433/mthack`

---

## Поток данных (end-to-end)

```
Эмулятор NDTP --(binary TCP, порт 9201)--> backend: TCP-сервер
   -> decoder.py: раскодировка G6CellNav00 в строку traffic.csv
   -> буферы в памяти (последние пакеты) + фоновая запись
        -> data/ndtp.csv  (CSV-контур)
        -> PostgreSQL: ndtp_telemetry
   -> ml-model (worker): читает ndtp_telemetry + schedule_stops
        -> build_features: 24 признака на момент T (анти-утечка)
        -> LightGBM model.txt -> прогноз задержки (сек, +/-)
        -> таблица predictions
   -> GET /api/predictions (backend) -> прогнозы и live-алерты
```

---

## ML-модуль

- **Модель:** LightGBM (`regression_l1`), файл `model.txt`, загружается один раз при старте
  сервиса (singleton `model_service.py`). Если модель недоступна — сервис не падает,
  работает в режиме деградации.
- **Признаки** (`features.py`, 24 шт.): горизонт прогноза, время/день недели, время с
  последнего пакета, последняя скорость/координаты, средние/макс/стд/доля остановок
  скорости за 5 и 10 минут, пройденное расстояние, плановый интервал до остановки,
  дистанция и «требуемая скорость» до целевой остановки, ETA-задержки, число остановок
  между, текущее отклонение `cur_dev_s`. Все признаки строятся **только по данным на
  момент T** — исключение утечки будущего.
- **Две задачи инференса:**
  1. **Офлайн (validate):** для каждой фиксированной точки `forecast_points (sample_id)`
     строятся признаки на момент T, прогноз пишется в `predictions`.
  2. **Live (на потоке):** `live_early_warning()` для каждого активного ТС находит ближайшую
     плановую остановку, чьё прибытие в окне **T+10…15 мин**, строит те же 24 признака и
     пишет алерт сразу в `predictions` с `sample_id = live_<tr_id>` (перезаписывается каждый
     цикл — всегда текущее состояние ТС).
- **Прогноз подписан:** `+` = опоздание, `−` = опережение (секунды).
- **Map Matching** (`map_matching.py`): привязка GPS-позиций к нити маршрута с учётом курса
  и скорости, результат пишется в `route_features`.

---

## Backend (API)

FastAPI, Swagger/OpenAPI на `http://localhost:8000/docs`.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/` | Визитка сервиса |
| GET | `/health` | Проверка живости |
| GET | `/api/cells` | Справочник ячеек NDTP (от эмулятора) |
| GET/POST | `/api/config` | Конфиг эмулятора (запуск/остановка потока) |
| GET | `/api/ndtp/decoded` | Последние раскодированные строки телеметрии |
| GET | `/api/ndtp/latest` | Актуальное состояние по каждому ТС |
| GET | `/api/predictions` | Все прогнозы и live-алерты (из БД) |
| POST | `/api/predictions` | Загрузка прогнозов по HTTP |
| GET | `/api/fleet/features` | Производные признаки всех ТС (скорость, простой, отклонение) |
| POST | `/api/whatif` | What-if: оценка выпуска доп. ТС на маршрут |
| GET | `/api/queue/status` | Состояние буферов приёма (без накопления очередей) |

Коды статусов: `200` успех, `422` неверный формат, `502` эмулятор недоступен.

---

## Производительность и надёжность

- **Latency инференса** логируется в `ml-model` строкой `[live] ... latency=NN.Nms`
  (время `build_features` + `model.predict` на цикл).
- **Без накопления очередей** — подтверждается `GET /api/queue/status` (`csv_queue_size`,
  `csv_dropped`); при работающем потоке очередь ≈ 0, отброшенных нет.
- **Деградация при обрыве связи:** сервис не падает — модель при недоступности возвращает
  `ready=False`, live-циклы помечают прогнозы флагом `stale` при «молчащем» потоке дольше
  45 с и продолжают работать по последним данным; TCP-приёмник переживает reconnect эмулятора.
- **Холодный старт** — весь стек поднимается одной командой `make up` (см. ниже).

---

## Структура таблиц БД

- `ndtp_telemetry` — приходящая телеметрия.
- `schedule_stops` — плановое расписание остановок (`tt_action_item_id`, `time_begin`, `tr_id`, `geom`).
- `forecast_points` — валидационные прогнозные точки (`sample_id`, `T`, `target_stop_id`, `target_time_begin`, `cur_dev_s`).
- `predictions` — прогнозы: `sample_id`, `prediction` (сек, ±), `stale`.
- `route_features` — результаты Map Matching.

---

## Запуск

### Требования

- **Docker Desktop** (Windows/Mac) или Docker Engine + Compose (Linux);
- **Make** — опционально (можно вместо него запускать команды `docker compose` напрямую);
- ~4 ГБ свободного места на диске (образы + данные).

### Шаг 1. Получить датасет

Датасет лежит в `dataset/` в корне проекта. Если каких-то файлов нет (например, при
клоне из git тяжёлые файлы могли не приехать), скачайте полный набор по ссылке:

> **https://drive.google.com/drive/folders/1LV_ge2XcOZWHD9RLR1qhcY8jzv-B8Toa**

Скачанные файлы должны лежать так:

```
dataset/
├── ndtp-telemetry-emulator.tar     ← образ эмулятора (обязателен для make setup)
├── train/traffic.csv, schedule.csv
├── test/traffic.csv, schedule.csv
├── validate/traffic.csv, schedule_plan.csv, points.csv
├── labels/labels_train.csv, labels_test.csv
└── sample_submission.csv
```

**Проверка, что данные на месте** (файлы должны быть непустыми):

```bash
# в PowerShell
Get-ChildItem dataset/validate
```
Ожидается: `points.csv (~12 КБ)`, `schedule_plan.csv (~680 КБ)`, `traffic.csv (~17 МБ)`.
Если `traffic.csv` или `schedule_plan.csv` пусты/отсутствуют — доскачайте их, иначе
воркер не сможет построить расписание и прогнозы.

### Шаг 2. Загрузить образ эмулятора (один раз)

```bash
make setup
```
Это идемпотентно: команда проверяет, загружен ли образ `ndtp-telemetry-emulator:1.0`,
и загружает его из `dataset/ndtp-telemetry-emulator.tar` только если его нет.

Без Make:
```bash
docker load -i dataset/ndtp-telemetry-emulator.tar
```

### Шаг 3. Поднять систему

```bash
make up
```
Эквивалент без Make:
```bash
docker compose up -d --build
```

Что происходит автоматически:
1. поднимаются контейнеры `ndtp-emu`, `db`, `backend`, `ml-model`;
2. backend сам отправляет конфиг эмулятору — поток телеметрии стартует;
3. `ml-model` заливает расписание/точки (если таблицы пусты), затем каждые ~20 с считает
   офлайн-прогнозы и live-алерты и пишет в `predictions`.

> ⏱️ **Первый прогноз появляется через ~20–60 секунд** после `make up`: воркеру нужно
> время на подключение к БД и первичную заливку расписания/телеметрии из CSV.

### Шаг 4. Проверить

```bash
make health                              # → {"status":"ok"}
make ps                                  # все 4 контейнера в статусе Up
```

Если `make health` вернул ошибку сразу после `make up` — подождите 10–20 секунд
(backend стартует с ожиданием БД) и повторите.

### Если телеметрия идёт, а прогнозов нет

Симптом: `count(*) FROM ndtp_telemetry` растёт, а `SELECT count(*) FROM predictions` = 0,
у воркера в логах ошибка `IsADirectoryError`.

Причина — известный баг **Docker Desktop на Windows/WSL**: при bind-mount крупных файлов
(`validate/traffic.csv` ~17 МБ, `schedule_plan.csv`) они могли смонтироваться **как каталоги**,
и воркер не может их прочитать. `up`/`down`/`restart` это часто не лечит.

Решение — **полный рестарт Docker Desktop** (иконка в трее → Restart / Quit→запустить
заново), затем ещё раз:

```bash
docker compose down
docker compose up -d --build
```

Альтернатива — скопировать сами файлы внутрь контейнера (не через bind-mount), изменив
`ml-model/Dockerfile` на `COPY` данных в образ.

### Проверка, что всё работает

```bash
make health         # → {"status":"ok"}
make db-show        # список таблиц (ndtp_telemetry, predictions, ...)
make logs           # следить за логами (в т.ч. [live] latency)
make ps             # статус контейнеров
```

После старта:
- Swagger/OpenAPI: **http://localhost:8000/docs**
- API: **http://localhost:8000** (корень — визитка)
- Эмулятор NDTP: **http://localhost:18080**

### Как посмотреть данные

```bash
# общее число строк телеметрии в БД
docker compose exec db psql -U mthack -d mthack -c "SELECT count(*) FROM ndtp_telemetry;"

# последние позиции ТС
docker compose exec db psql -U mthack -d mthack -c "SELECT unit_id, lon, lat, speed, receive_time FROM ndtp_telemetry ORDER BY id DESC LIMIT 5;"

# прогнозы (в т.ч. live-алерты по ТС)
docker compose exec db psql -U mthack -d mthack -c "SELECT * FROM predictions ORDER BY sample_id LIMIT 20;"
```

- `data/ndtp.csv` — телеметрия в CSV (запасной контур записи);
- `pgdata/` — физическое хранилище PostgreSQL.

### Остановка

```bash
make down
```
Останавливает контейнеры. Данные в `pgdata/` и `data/` сохраняются.

---

## Полезные цели Makefile

| Команда | Что делает |
|---|---|
| `make setup` | загрузить образ эмулятора NDTP (идемпотентно, только один раз) |
| `make up` | собрать и запустить всё |
| `make down` | остановить контейнеры |
| `make stop` / `make restart` | остановить / перезапустить контейнеры |
| `make logs` | следить за логами |
| `make health` | проверить, что сервис жив |
| `make cells` | справочник ячеек NDTP |
| `make ps` | статус контейнеров |
| `make db-psql` | интерактивный вход в БД |
| `make db-show` | список таблиц |

---

## Подключение к БД (для фронтенда/дашборда)

- Хост: `localhost`
- Порт: **5433**
- База: `mthack`
- Пользователь: `mthack`
- Пароль: `mthack`

URL: `postgresql://mthack:mthack@localhost:5433/mthack`

---

## Стек

Python 3.12, FastAPI, Uvicorn, asyncpg, LightGBM, pandas, numpy, PostgreSQL 16, Docker Compose.
ML и Backend упакованы в отдельные контейнеры, сборка по одной команде.