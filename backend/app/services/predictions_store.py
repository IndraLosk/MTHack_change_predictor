"""Кэш предсказаний в памяти (для быстрого ответа API без обращения к БД)."""

PREDICTIONS: dict[str, float] = {}


def upsert(items: list[dict]):
    """Сохраняет/обновляет предсказания по sample_id в кэше."""
    for item in items:
        sample_id = item.get("sample_id")
        prediction = item.get("prediction")
        if sample_id and prediction is not None:
            PREDICTIONS[str(sample_id)] = float(prediction)


def all() -> list[dict]:
    """Возвращает все кэшированные предсказания списком словарей."""
    return [
        {"sample_id": sample_id, "prediction": prediction}
        for sample_id, prediction in PREDICTIONS.items()
    ]