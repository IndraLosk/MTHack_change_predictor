from fastapi import APIRouter

from ..ndtp import receiver

router = APIRouter(prefix="/api/ndtp", tags=["telemetry"])


@router.get("/decoded")
async def ndtp_decoded(limit: int = 50):
    rows = receiver.recent(limit)
    return {
        "total": len(receiver.RECEIVED),
        "limit": limit,
        "rows": rows,
    }


@router.get("/latest")
async def ndtp_latest(limit: int = 500):
    rows = receiver.latest(limit)
    return {
        "total": len(rows),
        "limit": limit,
        "rows": rows,
    }