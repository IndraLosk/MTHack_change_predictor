# Запросы к backend и проверка статусов

Папка с готовыми запросами и скриптом автоматической проверки.

## Эндпоинты

| Метод | URL | Ожидаемый статус | Что делает |
|---|---|---|---|
| GET | `/health` | 200 | статус сервиса |
| GET | `/` | 200 | визитка сервиса |
| GET | `/api/cells` | 200 | справочник ячеек NDTP (содержит `G6CellNav00`) |
| GET | `/api/config` | 200 | текущий конфиг эмулятора |
| POST | `/api/config` | 200 | отправить конфиг (запустить/остановить поток) |
| GET | `/api/ndtp/decoded` | 200 | раскодированные строки телеметрии |

## Автопроверка статусов

```bash
# из корня проекта (контейнеры должны быть подняты: make up)
powershell -ExecutionPolicy Bypass -File requests/check-status.ps1
```

или через Makefile:

```bash
make check
```

Скрипт сам:
1. проверяет `/health`, `/`, `/api/cells`, `GET /api/config`;
2. запускает поток (`POST /api/config` с 1 юнитом);
3. ждёт 3 секунды;
4. проверяет, что `/api/ndtp/decoded` вернул строки;
5. останавливает поток (`POST /api/config` с `units: []`);
6. печатает PASS/FAIL по каждой проверке и общий итог.

## Ручные запросы (Postman)

### Запустить поток
```
POST http://localhost:8000/api/config
Content-Type: application/json
```
```json
{
  "targetHost": "backend",
  "targetPort": 9201,
  "units": [
    { "unitId": 1099984, "intervalMs": 1000, "autoGenerate": true, "cells": [] }
  ]
}
```

### Проверить получение телеметрии
```
GET http://localhost:8000/api/ndtp/decoded?limit=10
```

### Остановить поток
```
POST http://localhost:8000/api/config
Content-Type: application/json
```
```json
{
  "targetHost": "backend",
  "targetPort": 9201,
  "units": []
}
```