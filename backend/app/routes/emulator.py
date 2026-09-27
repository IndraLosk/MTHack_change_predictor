"""Эндпоинты-прокси к REST API эмулятора NDTP."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import emulator_client

router = APIRouter(prefix="/api", tags=["emulator"])

SPEC = {
    "summary": "Прокси к эмулятору NDTP",
    "description": "Передаёт запросы в REST API эмулятора (порт 18080).",
}


class EmulatorConfig(BaseModel):
    """Конфигурация эмулятора: куда слать телеметрию и какие юниты."""
    target_host: str | None = Field(None, description="Хост приёма телеметрии")
    target_port: int | None = Field(None, description="Порт приёма телеметрии")
    units: list = Field(default_factory=list, description="Эмулируемые устройства")


class ConfigUpdate(BaseModel):
    """Тело POST /api/config — запуск/остановка потока."""
    target_host: str = Field(..., description="Имя сервиса backend в docker-сети")
    target_port: int = Field(..., description="Порт TCP-приёмника backend")
    units: list = Field(default_factory=list, description="Юниты; [] = остановить поток")


@router.get("/cells", **SPEC)
async def cells():
    """Справочник поддерживаемых ячеек телематики эмулятора."""
    return await emulator_client.get_cells()


@router.get("/config", **SPEC)
async def config_get():
    """Текущий конфиг эмулятора (targetHost/targetPort/units)."""
    return await emulator_client.get_config()


@router.post("/config", **SPEC, response_model=None)
async def config_set(payload: ConfigUpdate):
    """Запустить/остановить поток телеметрии эмулятора."""
    return await emulator_client.set_config(payload.model_dump())