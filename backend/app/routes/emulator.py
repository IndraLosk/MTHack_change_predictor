"""Ручки-прокси к эмулятору NDTP."""

from fastapi import APIRouter

from ..services import emulator_client

router = APIRouter(prefix="/api", tags=["emulator"])


@router.get("/cells")
async def cells():
    """Справочник поддерживаемых ячеек телематики NDTP."""
    return await emulator_client.get_cells()


@router.get("/config")
async def config_get():
    """Текущий конфиг эмулятора (targetHost/targetPort/units)."""
    return await emulator_client.get_config()


@router.post("/config")
async def config_set(payload: dict):
    """Отправляет конфиг эмулятору: запустить/остановить поток."""
    return await emulator_client.set_config(payload)