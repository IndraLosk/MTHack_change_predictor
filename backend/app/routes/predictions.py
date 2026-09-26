from fastapi import APIRouter

from ..services import predictions_store

router = APIRouter(prefix="/api", tags=["predictions"])


@router.get("/predictions")
async def list_predictions():
    return {"count": len(predictions_store.PREDICTIONS), "items": predictions_store.all()}


@router.post("/predictions")
async def upsert_predictions(payload: list[dict]):
    predictions_store.upsert(payload)
    return {"accepted": len(payload)}