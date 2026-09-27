"""Ручки телеметрии NDTP."""

from fastapi import APIRouter

from ..ndtp import receiver

router = APIRouter(prefix="/api/ndtp", tags=["telemetry"])


@router.get("/decoded")
async def ndtp_decoded(limit: int = 50):
    """Последние раскодированные строки телеметрии (формат traffic.csv)."""
    rows = receiver.recent(limit)
    return {
        "total": receiver.total(),
        "limit": limit,
        "rows": rows,
    }


@router.get("/latest")
async def ndtp_latest(limit: int = 500):
    """Последнее состояние по каждому ТС (одна строка на ТС)."""
    rows = receiver.latest(limit)
    return {
        "total": len(rows),
        "limit": limit,
        "rows": rows,
    }