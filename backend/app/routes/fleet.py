"""Эндпоинты производных признаков, what-if и состояния очередей."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..ndtp import receiver
from ..services import derived_features

router = APIRouter(prefix="/api", tags=["fleet"])


class FleetFeature(BaseModel):
    """Производные признаки одного ТС."""
    tr_id: str
    n_points: int | None = Field(None, description="Число телеметрических точек")
    avg_speed_kmh: float | None = Field(None, description="Средняя скорость за окно")
    idle_points: int | None = Field(None, description="Число точек в простое (speed<3)")
    idle_share: float | None = Field(None, description="Доля времени простоя, 0..1")
    route_len_m: float | None = None
    route_progress_m: float | None = None
    route_progress_frac: float | None = Field(None, description="Доля пройденного маршрута")
    next_stop_index: int | None = None
    seg_speed_kmh: float | None = Field(None, description="Скорость на текущем сегменте")
    deviation_s: float | None = Field(None, description="Текущее отклонение от графика, сек")


class FleetFeaturesResponse(BaseModel):
    """Признаки всех активных ТС парка."""
    tr_id: list[str] = Field(default_factory=list)
    features: list[FleetFeature] = Field(default_factory=list)


class WhatifRequest(BaseModel):
    """Сценарий what-if: выпуск дополнительных ТС на маршрут."""
    tr_id: str = Field(..., description="Нить маршрута (ID ТС)")
    add_vehicles: int = Field(1, ge=0, description="Сколько доп. ТС выпустить")


class WhatifResponse(BaseModel):
    """Результат симуляции: метрики «до/после»."""
    tr_id: str
    route_len_m: float
    active_vehicles: int
    add_vehicles: int
    avg_speed_kmh: float
    interval_before_s: float
    interval_after_s: float
    delay_before_s: float
    delay_after_s: float


class QueueStatusResponse(BaseModel):
    """Состояние буферов приёма телеметрии."""
    received_total: int = Field(..., description="Строк в буфере истории")
    csv_queue_size: int = Field(..., description="Строк в очереди на запись")
    csv_queue_maxlen: int = Field(..., description="Макс. размер очереди")
    csv_dropped: int = Field(..., description="Отброшено строк при переполнении")


@router.get("/fleet/features", response_model=FleetFeaturesResponse)
async def fleet_features(request: Request):
    """Производные признаки парка: скорость, простой, позиция на маршруте."""
    tele = receiver.recent(1000)
    db = getattr(request.app.state, "db", None)
    out = []
    if tele and db is not None:
        sched = await db.fetch_schedule_stops()
        raw = derived_features.fleet_features(tele, sched)
        for f in raw:
            if "tr_id" in f and f["tr_id"] is not None:
                f["tr_id"] = str(f["tr_id"])
            out.append(f)
    return FleetFeaturesResponse(tr_id=[f["tr_id"] for f in out], features=out)


@router.post("/whatif", response_model=WhatifResponse)
async def whatif(payload: WhatifRequest, request: Request):
    """What-if: оценивает влияние выпуска доп. ТС на интервалы маршрута."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=400, detail="БД недоступна")

    sched = await db.fetch_schedule_stops()
    tele = receiver.recent(1000)
    result = derived_features.whatif_add_vehicle(sched, tele, payload.tr_id, payload.add_vehicles)
    if result is None:
        raise HTTPException(status_code=404, detail=f"нет данных по маршруту {payload.tr_id}")
    return result


@router.get("/queue/status", response_model=QueueStatusResponse)
async def queue_status():
    """Состояние буферов приёма: подтверждение отсутствия накопления."""
    return QueueStatusResponse(
        received_total=receiver.total(),
        csv_queue_size=len(receiver.CSV_QUEUE),
        csv_queue_maxlen=receiver.CSV_QUEUE.maxlen,
        csv_dropped=receiver.csv_dropped(),
    )