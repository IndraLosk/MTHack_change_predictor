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