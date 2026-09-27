"""Системные эндпоинты: визитка сервиса и проверка готовности."""

from fastapi import APIRouter
from pydantic import BaseModel

from .. import settings

router = APIRouter(tags=["system"])


class RootResponse(BaseModel):
    """Схема ответа визитки сервиса."""
    service: str
    docs: str
    emulator: str
    ndtp_receive_port: int


class HealthResponse(BaseModel):
    """Схема ответа проверки живости."""
    status: str


@router.get("/", response_model=RootResponse)
async def root() -> RootResponse:
    """Возвращает визитку сервиса и его актуальную конфигурацию."""
    return RootResponse(
        service="MTHack backend",
        docs="/docs",
        emulator=settings.NDTP_EMULATOR_URL,
        ndtp_receive_port=settings.NDTP_RECEIVE_PORT,
    )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Возвращает статус живости сервиса для healthcheck."""
    return HealthResponse(status="ok")