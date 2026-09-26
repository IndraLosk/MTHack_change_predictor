# MTHack Backend — API Reference

Backend для хакатона Московского транспорта.
Базовый URL: `http://localhost:8000`
Интерактивный Swagger: `http://localhost:8000/docs` (также `GET /openapi.json` — OpenAPI-схема).

## Сводка эндпоинтов

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/` | Визитка сервиса |
| GET | `/health` | Статус сервиса |
| GET | `/api/cells` | Справочник ячеек NDTP |
| GET | `/api/config` | Текущий конфиг эмулятора |
| POST | `/api/config` | Отправить конфиг эмулятору |
| GET | `/api/ndtp/decoded` | Раскодированные строки телеметрии NDTP |
| GET | `/api/ndtp/latest` | Последнее известное состояние по каждому ТС |
| GET | `/api/predictions` | Принятые предсказания |
| POST | `/api/predictions` | Загрузить предсказания |

---

## System

### `GET /`
Визитка сервиса: имя, ссылка на docs, адрес эмулятора, порт приёма NDTP.

**Ответ:** `200`
```json
{
  "service": "MTHack backend",
  "docs": "/docs",
  "emulator": "http://ndtp-emu:18080",
  "ndtp_receive_port": 9201
}
```

### `GET /health`
Проверка, что сервис жив.

**Ответ:** `200`
```json
{ "status": "ok" }
```

---

## Emulator (прокси к эмулятору NDTP)

### `GET /api/cells`
Возвращает справочник поддерживаемых типов ячеек телематики (G6CellNav00 и др.) от эмулятора.

**Ответ:** `200` — массив ячеек; `502` — эмулятор недоступен.

### `GET /api/config`
Возвращает текущий конфиг эмулятора (targetHost, targetPort, units).

**Ответ:** `200`
```json
{ "targetHost": "backend", "targetPort": 9201, "units": [] }
```

### `POST /api/config`
Отправить конфиг эмулятору — запустить/остановить поток телеметрии.

**Тело (JSON):**
```json
{
  "targetHost": "backend",
  "targetPort": 9201,
  "units": [
    { "unitId": 1099984, "intervalMs": 1000, "autoGenerate": true, "cells": [] }
  ]
}
```
- `targetHost` — имя сервиса в docker-compose сети (обычно `backend`);
- `targetPort` — порт TCP-приёмника backend (9201);
- `units[]` — список устройств; `units: []` останавливает поток.

**Ответ:** `200` — конфиг принят и применён; `502` — эмулятор недоступен.

---

## Telemetry

### `GET /api/ndtp/decoded`
Последние раскодированные строки телеметрии NDTP (в формате колонок `traffic.csv`).

**Параметр (query):** `limit` (int, по умолчанию 50) — сколько последних строк вернуть.

**Ответ:** `200`
```json
{
  "total": 14,
  "limit": 50,
  "rows": [
    {
      "event_time": 1790348614,
      "location_valid": true,
      "lon": 37.5983,
      "lat": 55.7983,
      "alt": 211,
      "speed": 17,
      "heading": 222,
      "nsat": 8,
      "pdop": 1,
      "unit_id": 1099984,
      "tr_id": 1099984,
      "raw_hex": "7e7e7b000000..."
    }
  ]
}
```

### `GET /api/ndtp/latest`
Последнее известное состояние по каждому ТС — одна строка на устройство (поле обновляется при каждом новом пакете). Удобно для дашборда: показывает «где сейчас каждый автобус».

**Параметр (query):** `limit` (int, по умолчанию 500).

**Ответ:** тот же формат строк, что в `/api/ndtp/decoded`.

---

## Predictions

### `GET /api/predictions`
Возвращает все принятые предсказания.

**Ответ:** `200`
```json
{ "count": 151, "items": [ { "sample_id": "131672_1767670500", "prediction": 274.0 } ] }
```

### `POST /api/predictions`
Загрузить предсказания. Формат — JSON-массив объектов `{ sample_id, prediction }` (как их присылает ML-модуль).

**Тело (JSON):**
```json
[
  { "sample_id": "131672_1767670500", "prediction": 274.0 },
  { "sample_id": "122048_1767732000", "prediction": 45.0 }
]
```

**Ответ:** `200`
```json
{ "accepted": 2 }
```
Если нет тела или неверный формат — `422`.

---

## Коды статусов
| Код | Значение |
|---|---|
| 200 | Успех |
| 422 | Неверный формат тела/параметров (валидация FastAPI) |
| 502 | Эмулятор NDTP недоступен |