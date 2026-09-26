# MTHack_change_predictor

Backend для хакатона Московского транспорта: приём телеметрии NDTP, её накопление в PostgreSQL и CSV, API для предсказаний.

## Требования
- Docker (Desktop на Windows)
- Make (или запуск через команды `docker compose` напрямую)

## Запуск backend

Из PowerShell в корне проекта:

```bash
make up
```

Что происходит автоматически:
1. поднимаются 3 контейнера: `ndtp-emu` (эмулятор телеметрии), `db` (PostgreSQL), `backend` (FastAPI);
2. backend сам отправляет конфиг эмулятору — поток телеметрии запускается без ручных действий;
3. каждые 5 секунд телеметрия пишется и в PostgreSQL, и в CSV.

## Проверка, что всё работает

```bash
make health        # → {"status":"ok"}
make db-show       # список таблиц (ndtp_telemetry, predictions)
```

- Swagger/OpenAPI: `http://localhost:8000/docs`
- API: `http://localhost:8000` (корень — визитка сервиса)

## Как посмотреть данные

```bash
# общее число строк телеметрии в БД
docker compose exec db psql -U mthack -d mthack -c "SELECT count(*) FROM ndtp_telemetry;"

# последние позиции ТС
docker compose exec db psql -U mthack -d mthack -c "SELECT unit_id, lon, lat, speed, receive_time FROM ndtp_telemetry ORDER BY id DESC LIMIT 5;"
```

- `data/ndtp.csv` — телеметрия в CSV (запасной контур записи)
- `pgdata/` — физическое хранилище PostgreSQL

## Содержимое таблиц

```bash
# вся таблица предсказаний
docker compose exec db psql -U mthack -d mthack -c "SELECT * FROM predictions;"

# последние N строк телеметрии
docker compose exec db psql -U mthack -d mthack -c "SELECT * FROM ndtp_telemetry ORDER BY id DESC LIMIT 10;"

# всё содержимое tableы телеметрии разом (без LIMIT — может быть много)
docker compose exec db psql -U mthack -d mthack -c "SELECT * FROM ndtp_telemetry;"
```

Или интерактивно: `make db-psql` — попадёшь в SQL-консоль и вводишь запросы прямо там.

## Остановка

```bash
make down
```
Останавливает контейнеры. Данные в `pgdata/` и `data/` сохраняются.

## Полезные цели Makefile

| Команда | Что делает |
|---|---|
| `make up` | собрать и запустить всё |
| `make down` | остановить контейнеры |
| `make logs` | следить за логами |
| `make health` | проверить, что сервис жив |
| `make ps` | статус контейнеров |
| `make db-psql` | интерактивный вход в БД |
| `make db-show` | список таблиц |

## Подключение к БД (для фронтенда)

- Хост: `localhost`
- Порт: **5433**
- База: `mthack`
- Пользователь: `mthack`
- Пароль: `mthack`

URL: `postgresql://mthack:mthack@localhost:5433/mthack`