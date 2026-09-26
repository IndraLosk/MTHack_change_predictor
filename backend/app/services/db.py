"""Работа с PostgreSQL: подключение, схема, чтение/запись данных."""

import asyncio
from datetime import datetime

import asyncpg

from .. import settings


def _to_dt(value):
    """Преобразует строку времени в datetime (или None)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class Database:
    """Обёртка над пулом соединений asyncpg."""

    def __init__(self):
        self.pool = None

    @property
    def dsn(self) -> str:
        """Строка подключения к PostgreSQL."""
        return (
            f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}"
            f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
        )

    async def connect(self):
        """Создаёт пул соединений и гарантирует наличие таблиц."""
        for attempt in range(30):
            try:
                self.pool = await asyncpg.create_pool(self.dsn)
                await self.ensure_tables()
                print(f"[db] подключено: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}", flush=True)
                return
            except Exception as exc:
                print(f"[db] попытка {attempt + 1}: {exc}", flush=True)
                await asyncio.sleep(2)
        raise RuntimeError("не удалось подключиться к PostgreSQL")

    async def ensure_tables(self):
        """Создаёт таблицы ndtp_telemetry и predictions, если их нет."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ndtp_telemetry (
                    id BIGSERIAL PRIMARY KEY,
                    packet_id TEXT,
                    tr_id TEXT,
                    unit_id TEXT,
                    event_time TEXT,
                    device_event_id TEXT,
                    location_valid BOOLEAN,
                    gps_time TEXT,
                    lon DOUBLE PRECISION,
                    lat DOUBLE PRECISION,
                    alt DOUBLE PRECISION,
                    speed DOUBLE PRECISION,
                    heading DOUBLE PRECISION,
                    receive_time TIMESTAMPTZ,
                    is_hist_data BOOLEAN
                );
                CREATE TABLE IF NOT EXISTS predictions (
                    sample_id TEXT PRIMARY KEY,
                    prediction DOUBLE PRECISION
                );
            """)

    async def insert_ndtp(self, rows: list[dict]):
        """Вставляет пачку телеметрии в таблицу ndtp_telemetry."""
        if not rows:
            return
        tuples = []
        for r in rows:
            tuples.append((
                r.get("packet_id"), r.get("tr_id"), r.get("unit_id"),
                r.get("event_time"), r.get("device_event_id"),
                r.get("location_valid"), r.get("gps_time"),
                r.get("lon"), r.get("lat"), r.get("alt"),
                r.get("speed"), r.get("heading"),
                _to_dt(r.get("receive_time")), r.get("is_hist_data"),
            ))
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO ndtp_telemetry
                    (packet_id, tr_id, unit_id, event_time, device_event_id,
                     location_valid, gps_time, lon, lat, alt, speed, heading,
                     receive_time, is_hist_data)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                tuples,
            )

    async def upsert_predictions(self, rows: list[dict]):
        """Записывает предсказания, обновляя существующие sample_id."""
        if not rows:
            return
        tuples = [(str(r.get("sample_id")), float(r.get("prediction"))) for r in rows]
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO predictions (sample_id, prediction)
                VALUES ($1, $2)
                ON CONFLICT (sample_id) DO UPDATE
                    SET prediction = EXCLUDED.prediction
                """,
                tuples,
            )

    async def get_predictions(self) -> list:
        """Возвращает все предсказания из БД."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT sample_id, prediction FROM predictions ORDER BY sample_id")
        return [{"sample_id": r["sample_id"], "prediction": r["prediction"]} for r in rows]

    async def get_ndtp(self, limit: int = 100) -> list:
        """Возвращает последние строки телеметрии из БД."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM ndtp_telemetry ORDER BY id DESC LIMIT $1", limit
            )
        return [dict(r) for r in rows]

    async def close(self):
        """Закрывает пул соединений."""
        if self.pool:
            await self.pool.close()