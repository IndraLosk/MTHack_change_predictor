"""Системные ручки: визитка и статус сервиса."""

from fastapi import APIRouter

from .. import settings

router = APIRouter(tags=["system"])


@router.get("/")
async def root():
    """Визитка сервиса: имя, docs, адрес эмулятора, порт приёма."""
    return {
        "service": "MTHack backend",
        "docs": "/docs",
        "emulator": settings.NDTP_EMULATOR_URL,
        "ndtp_receive_port": settings.NDTP_RECEIVE_PORT,
    }


@router.get("/health")
async def health():
    """Проверка, что сервис жив."""
    return {"status": "ok"}