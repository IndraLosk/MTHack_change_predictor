import httpx
from fastapi import HTTPException

from ..settings import NDTP_EMULATOR_URL


async def _request(method: str, path: str, payload: dict | None = None):
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
    return await _request("GET", "/api/cells")


async def get_config():
    return await _request("GET", "/api/config")


async def set_config(payload: dict):
    return await _request("POST", "/api/config", payload)