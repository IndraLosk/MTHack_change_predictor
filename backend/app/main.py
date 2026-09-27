"""Точка входа: создание приложения, регистрация роутеров и жизненный цикл."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI

from . import settings
from .ndtp import receiver
from .routes import emulator, fleet, predictions, system, telemetry
from .services.db import Database


async def _auto_configure_emulator():
    """Отправляет конфиг эмулятору при старте"""
    path = settings.NDTP_AUTO_CONFIG
    if not os.path.exists(path):
        print(f"[auto] конфиг эмулятора не найден: {path}", flush=True)
        return
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)

    for attempt in range(10):
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.post(
                    f"{settings.NDTP_EMULATOR_URL}/api/config", json=payload
                )
            resp.raise_for_status()
            print(
                f"[auto] эмулятор настроен: {payload.get('targetHost')}:"
                f"{payload.get('targetPort')}, юнитов={len(payload.get('units', []))}",
                flush=True,
            )
            return
        except Exception as exc:
            print(f"[auto] попытка {attempt + 1}: {exc}", flush=True)
            await asyncio.sleep(1)
    print("[auto] не удалось настроить эмулятор", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управляет жизненным циклом: инициализирует и гасит ресурсы приложения."""
    db = Database()
    await db.connect()
    app.state.db = db

    server = await receiver.start_receiver(settings.NDTP_RECEIVE_PORT)
    app.state.ndtp_server = server
    writer_task = asyncio.create_task(
        receiver.csv_writer_loop(settings.NDTP_CSV_PATH, settings.NDTP_CSV_INTERVAL, db)
    )
    app.state.ndtp_csv_task = writer_task

    if settings.NDTP_AUTO_START:
        await _auto_configure_emulator()

    yield
    writer_task.cancel()
    receiver.flush_rows_to_csv(
        settings.NDTP_CSV_PATH,
        receiver.rows_to_traffic_format(
            receiver._drain_csv_queue(), datetime.now(timezone.utc)
        ),
    )
    server.close()
    await server.wait_closed()
    await db.close()


app = FastAPI(
    title="MTHack Backend",
    description="Backend для хакатона Московского транспорта: приём NDTP, признаки, API.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(system.router)
app.include_router(emulator.router)
app.include_router(telemetry.router)
app.include_router(predictions.router)
app.include_router(fleet.router)