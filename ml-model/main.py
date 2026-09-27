"""FastAPI-обёртка ML-модели для связи с backend.

Принимает JSON с точками прогноза (и опционально телеметрией/расписанием),
строит признаки через features.build_features и возвращает предсказания.

Если телеметрия/расписание не переданы — подтягиваются из файлов
TRAFFIC_PATH / SCHEDULE_PATH (по умолчанию validate-данные).
"""

import os

import pandas as pd
from fastapi import FastAPI, HTTPException

from model_service import model

app = FastAPI(title="MTHack ML", version="0.1.0")

TRAFFIC_PATH = os.getenv("TRAFFIC_PATH", "/app/data/traffic.csv")
SCHEDULE_PATH = os.getenv("SCHEDULE_PATH", "/app/data/schedule_plan.csv")


def _load_df_from_file(path: str):
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame()


@app.get("/health")
async def health():
    return model.health()


@app.post("/predict")
async def predict(payload: dict):
    """Предиктор: принимает points (+ traffic/schedule), возвращает предсказания."""
    if not model.ready:
        return {"ready": False, "error": model.error}

    points = pd.DataFrame(payload.get("points", []))
    if points.empty:
        return {"ready": True, "predictions": []}

    for col in ("tr_id", "target_stop_id"):
        if col in points.columns:
            points[col] = pd.to_numeric(points[col], errors="coerce").astype("Int64")
    for col in ("cur_dev_s",):
        if col in points.columns:
            points[col] = pd.to_numeric(points[col], errors="coerce").astype("float64")

    traffic = pd.DataFrame(payload.get("traffic", []) or [])
    schedule = pd.DataFrame(payload.get("schedule", []) or [])
    if traffic.empty:
        traffic = _load_df_from_file(TRAFFIC_PATH)
    if schedule.empty:
        schedule = _load_df_from_file(SCHEDULE_PATH)

    try:
        preds = model.predict(points, traffic, schedule)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if preds is None:
        return {"ready": True, "predictions": []}

    predictions = [
        {"sample_id": str(sid), "prediction": round(float(value), 1)}
        for sid, value in preds.items()
    ]
    return {"ready": True, "predictions": predictions}