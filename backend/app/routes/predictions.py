"""Ручки предсказаний: просмотр и загрузка."""

from fastapi import APIRouter, Request

from ..services import predictions_store

router = APIRouter(prefix="/api", tags=["predictions"])


@router.get("/predictions")
async def list_predictions(request: Request):
    """Возвращает все принятые предсказания."""
    rows = predictions_store.all()
    db = getattr(request.app.state, "db", None)
    if db is not None and not rows:
        rows = await db.get_predictions()
    return {"count": len(rows), "items": rows}


@router.post("/predictions")
async def upsert_predictions(payload: list[dict], request: Request):
    """Загружает предсказания (массив {sample_id, prediction})."""
    predictions_store.upsert(payload)
    db = getattr(request.app.state, "db", None)
    if db is not None:
        await db.upsert_predictions(payload)
    return {"accepted": len(payload)}