"""Эндпоинты живых данных телеметрии NDTP."""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..ndtp import receiver

router = APIRouter(prefix="/api/ndtp", tags=["telemetry"])


class TelemetryRow(BaseModel):
    """Одна раскодированная строка телеметрии NDTP."""
    event_time: int | None = Field(None, description="Unix-время пакета")
    location_valid: bool = Field(False, description="Достоверность координат")
    lon: float | None = Field(None, description="Долгота")
    lat: float | None = Field(None, description="Широта")
    alt: float | None = Field(None, description="Высота, м")
    speed: float | None = Field(None, description="Скорость, км/ч")
    heading: float | None = Field(None, description="Курс, °")
    nsat: int | None = None
    pdop: float | None = None
    unit_id: int | None = Field(None, description="ID бортового терминала")
    tr_id: str | None = Field(None, description="ID транспортного средства")
    raw_hex: str | None = Field(None, description="Сырой кадр в hex (для отладки)")


class TelemetryResponse(BaseModel):
    """Ответ с последними строками телеметрии."""
    total: int = Field(..., description="Всего строк в буфере")
    limit: int = Field(..., description="Запрошенный лимит")
    rows: list[TelemetryRow] = Field(default_factory=list, description="Строки телеметрии")


@router.get("/decoded", response_model=TelemetryResponse)
async def ndtp_decoded(limit: int = Query(50, ge=1, le=5000)):
    """Возвращает последние limit раскодированных строк телеметрии."""
    rows = _normalize_rows(receiver.recent(limit))
    return TelemetryResponse(total=receiver.total(), limit=limit, rows=rows)


@router.get("/latest", response_model=TelemetryResponse)
async def ndtp_latest(limit: int = Query(500, ge=1, le=5000)):
    """Возвращает актуальное состояние каждого ТС (одна строка на ТС)."""
    rows = _normalize_rows(receiver.latest(limit))
    return TelemetryResponse(total=len(rows), limit=limit, rows=rows)


def _normalize_rows(rows: list) -> list:
    """Приводит tr_id/unit_id к строке, чтобы прошла валидация Pydantic."""
    out = []
    for r in rows:
        r = dict(r)
        if "tr_id" in r and r["tr_id"] is not None:
            r["tr_id"] = str(r["tr_id"])
        if "unit_id" in r and r["unit_id"] is not None:
            r["unit_id"] = int(r["unit_id"]) if str(r["unit_id"]).isdigit() else r["unit_id"]
        out.append(r)
    return out