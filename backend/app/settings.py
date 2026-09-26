"""Настройки приложения, читаемые из переменных окружения."""

import os

NDTP_EMULATOR_URL = os.getenv("NDTP_EMULATOR_URL", "http://localhost:18080")
NDTP_RECEIVE_PORT = int(os.getenv("NDTP_RECEIVE_PORT", "9201"))
NDTP_CSV_PATH = os.getenv("NDTP_CSV_PATH", "/app/data/ndtp.csv")
NDTP_CSV_INTERVAL = int(os.getenv("NDTP_CSV_INTERVAL", "60"))
NDTP_AUTO_START = os.getenv("NDTP_AUTO_START", "true").lower() in ("1", "true", "yes")
NDTP_AUTO_CONFIG = os.getenv("NDTP_AUTO_CONFIG", "/app/config/emulator-config.json")

DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_USER = os.getenv("DB_USER", "mthack")
DB_PASSWORD = os.getenv("DB_PASSWORD", "mthack")
DB_NAME = os.getenv("DB_NAME", "mthack")