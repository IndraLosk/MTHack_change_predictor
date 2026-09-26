"""HTTP-клиент для общения с эмулятором NDTP."""

import httpx
from fastapi import HTTPException

from ..settings import NDTP_EMULATOR_URL


async def _request(method: str, path: str, payload: dict | None = None):
    """Выполняет HTTP-запрос к эмулятору; при недоступности — 502."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.request(method, f"{NDTP_EMULATOR_URL}{path}", json=payload)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"NDTP emulator unreachable ({NDTP_EMULATOR_URL}): {exc}",
        )


async def get_cells():
    """Справочник ячеек эмулятора."""
    return await _request("GET", "/api/cells")


async def get_config():
    """Текущий конфиг эмулятора."""
    return await _request("GET", "/api/config")


async def set_config(payload: dict):
    """Отправляет конфиг эмулятору."""
    return await _request("POST", "/api/config", payload)