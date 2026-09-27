"""
Сервисная обёртка модели для бэкенда.

Принцип: модель загружается из файла ОДИН раз при старте сервиса, дальше используется только predict.

Использование (FastAPI и любой другой фреймворк):

    from model_service import model   # singleton, грузится при импорте

    @app.post('/predict')
    def predict(payload):
        preds = model.predict(points_df, traffic_df, schedule_df)
        return {'predictions': preds.round(1).to_dict()}

Путь к модели задаётся переменной окружения MODEL_PATH (по умолчанию model.txt рядом со скриптом) — это позволяет подменять версию модели без правки кода.

Если файл модели недоступен при старте, сервис не падает — model.ready=False, predict вернёт None,
и бэкенд может отдать baseline cur_dev_s или последний известный прогноз.
"""
import os

import pandas as pd

from features import build_features, FEATURES

MODEL_PATH = os.getenv(
    'MODEL_PATH',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model.txt'))


class DelayModelService:
    """Тонкая обёртка над LightGBM Booster для инференса в бэкенде."""

    def __init__(self, model_path=MODEL_PATH):
        import lightgbm as lgb
        self.booster = None
        self.error = None
        try:
            self.booster = lgb.Booster(model_file=model_path)
        except Exception as e:          # деградация без падения сервиса
            self.error = str(e)

    @property
    def ready(self):
        return self.booster is not None

    def predict(self, points: pd.DataFrame, traffic: pd.DataFrame,
                schedule: pd.DataFrame):
        """
        points/traffic/schedule -> pd.Series прогнозов задержки (сек),
        индекс = sample_id. None, если модель не загружена.
        """
        if not self.ready:
            return None
        feats = build_features(points, traffic, schedule)
        return pd.Series(self.booster.predict(feats[FEATURES]), index=feats.index)

    def health(self):
        """Статус для /health эндпоинта бэкенда."""
        return {'ready': self.ready, 'model_path': MODEL_PATH, 'error': self.error}


# singleton: загружается один раз при импорте модуля
model = DelayModelService()
