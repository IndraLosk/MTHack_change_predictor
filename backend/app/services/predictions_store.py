"""Хранилище предсказаний в памяти (заглушка до перехода на БД)."""

PREDICTIONS: dict[str, float] = {}


def upsert(items: list[dict]):
    """Сохраняет/обновляет предсказания по sample_id."""
    for item in items:
        sample_id = item.get("sample_id")
        prediction = item.get("prediction")
        if sample_id and prediction is not None:
            PREDICTIONS[str(sample_id)] = float(prediction)


def all() -> list[dict]:
    """Возвращает все предсказания."""
    return [
        {"sample_id": sample_id, "prediction": prediction}
        for sample_id, prediction in PREDICTIONS.items()
    ]