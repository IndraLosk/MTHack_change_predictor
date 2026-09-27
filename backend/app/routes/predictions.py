"""Эндпоинты предсказаний задержек (чтение из кэша/БД, загрузка)."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..services import predictions_store

router = APIRouter(prefix="/api", tags=["predictions"])


class PredictionItem(BaseModel):
    """Одно предсказание: идентификатор прогнозной точки и задержка (сек)."""
    sample_id: str = Field(..., description="ID прогнозной точки из validate/points")
    prediction: float = Field(..., description="Прогнозируемая задержка, сек")


class PredictionsResponse(BaseModel):
    """Список всех принятых предсказаний."""
    count: int = Field(..., description="Число предсказаний")
    items: list[PredictionItem] = Field(default_factory=list)


class AcceptedResponse(BaseModel):
    """Результат загрузки предсказаний."""
    accepted: int = Field(..., description="Сколько записей принято")


@router.get("/predictions", response_model=PredictionsResponse)
async def list_predictions(request: Request):
    """Возвращает все предсказания (из кэша или БД)."""
    rows = predictions_store.all()
    db = getattr(request.app.state, "db", None)
    if db is not None and not rows:
        rows = await db.get_predictions()
    return PredictionsResponse(count=len(rows), items=rows)


@router.post("/predictions", response_model=AcceptedResponse)
async def upsert_predictions(payload: list[PredictionItem], request: Request):
    """Загружает предсказания, сохраняя их в кэш и БД."""
    items = [p.model_dump() for p in payload]
    predictions_store.upsert(items)
    db = getattr(request.app.state, "db", None)
    if db is not None:
        await db.upsert_predictions(items)
    return AcceptedResponse(accepted=len(items))