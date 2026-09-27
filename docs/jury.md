# Инструкция для жюри — как запустить и проверить систему

Диспетчерская ИИ-система раннего прогнозирования отклонений городского транспорта.

---

## 1. Требования

- Docker Desktop (Windows/Mac) или Docker Engine + Compose (Linux)
- ~4 ГБ свободного места
- Make (опционально; без него — команды `docker compose`)

## 2. Датасет

Скачать и распаковать полный датасет в папку `dataset/` проекта:

**Ссылка на датасет:** `https://drive.google.com/drive/folders/1LV_ge2XcOZWHD9RLR1qhcY8jzv-B8Toa`

Структура (в README есть полная таблица). Минимально для запуска нужны:
- `dataset/ndtp-telemetry-emulator.tar`
- `dataset/validate/traffic.csv` (~17 МБ)
- `dataset/validate/schedule_plan.csv` (~680 КБ)
- `dataset/validate/points.csv`

Проверка, что данные на месте: `Get-ChildItem dataset/validate` (в PowerShell).

## 3. Запуск

В корне проекта:

```bash
make setup    # один раз: загрузить образ эмулятора NDTP из dataset/ndtp-telemetry-emulator.tar
make up       # собрать и поднять 4 контейнера: ndtp-emu, db, backend, ml-model
```

Без Make:
```bash
docker load -i dataset/ndtp-telemetry-emulator.tar
docker compose up -d --build
```

При `make up`:
1. поднимаются `ndtp-emu`, `db` (PostgreSQL), `backend` (FastAPI), `ml-model` (LightGBM-воркер);
2. backend сам настраивает эмулятор — поток телеметрии NDTP стартует по TCP :9201;
3. воркер заливает расписание/точки (если таблицы пусты), затем каждые ~20 с считает
   офлайн-прогнозы и live-алерты и пишет их в PostgreSQL (`predictions`).
4. Дашборд Grafana стартует отдельной командой `make front` (нужен pull образа из Docker Hub).

Первый прогноз появляется через ~20–60 секунд после старта.

## 4. Где увидеть прогнозы и алерты

В таблице `predictions`:

```bash
docker compose exec db psql -U mthack -d mthack -c "SELECT * FROM predictions LIMIT 20;"
```

- `sample_id ..._<unix>` — офлайн-прогноз по валидационной точке (задержка в сек, ±);
- `sample_id = live_<tr_id>` — live-алерт по ТС (перезаписывается каждый цикл);
- `prediction` — прогнозируемая задержка в секундах: `+` опоздание, `−` опережение;
- `stale` — флаг деградации: `true`, если поток телеметрии молчит дольше 45 с
  (система продолжает работать по последним данным).

Живая телеметрия (последние позиции ТС):

```bash
GET http://localhost:8000/api/ndtp/latest
```

## 5. Как открыть дашборд

Дашборд (BI-модуль): **Grafana http://localhost:3000** (логин/пароль `admin/admin`).

После `make up` запусти `make front` (поднимет контейнер Grafana; для pull образа нужен
доступ к Docker Hub). Провижинится автоматически из `grafana/`:
- **Карта парка** (Geomap): каждая ТС — маркер на карте, цвет по прогнозу задержки
  (зелёный < 30 с, жёлтый 30–120 с, красный > 120 с); серые точки — остановки «нить маршрута».
  Автообновление 5 с — живая картинка вслед за потоком.
- **Live-алерты**: таблица прогнозов `live_<tr>` с `prediction > 30 с` (`stale = false`).
- **Статы**: активных ТС, число событий риска, состояние потока (STALE при паузе > 45 с).
- **Unified Alerting**: правило `mthack-live-risk` — алерт при пиковой прогнозируемой
  задержке парка > 120 с.

## 6. API и метрики

- Swagger/OpenAPI: **http://localhost:8000/docs** (схема: /openapi.json)
- Корень API: **http://localhost:8000** (визитка)
- Эмулятор NDTP: **http://localhost:18080**

Подтверждение «без накопления очередей»:

```bash
GET http://localhost:8000/api/queue/status
```
→ `csv_queue_size ≈ 0`, `csv_dropped = 0` при работающем потоке.

Latency инференса — в логах воркера:

```bash
docker compose logs ml-model --tail 50
```
→ строка вида `[live] ... latency=NN.Nms`.

## 7. Остановка

```bash
make down
```
Останавливает контейнеры; данные в `pgdata/` и `data/` сохраняются.

## 8. Подключение к БД (для дашборда)

- Хост: `localhost`, Порт: **5433**, База: `mthack`, Пользователь/пароль: `mthack`
- URL: `postgresql://mthack:mthack@localhost:5433/mthack`