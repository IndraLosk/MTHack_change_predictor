PREDICTIONS: dict[str, float] = {}


def upsert(items: list[dict]):
    for item in items:
        sample_id = item.get("sample_id")
        prediction = item.get("prediction")
        if sample_id and prediction is not None:
            PREDICTIONS[str(sample_id)] = float(prediction)


def all() -> list[dict]:
    return [
        {"sample_id": sample_id, "prediction": prediction}
        for sample_id, prediction in PREDICTIONS.items()
    ]