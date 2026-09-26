import os

NDTP_EMULATOR_URL = os.getenv("NDTP_EMULATOR_URL", "http://localhost:18080")
NDTP_RECEIVE_PORT = int(os.getenv("NDTP_RECEIVE_PORT", "9201"))
NDTP_CSV_PATH = os.getenv("NDTP_CSV_PATH", "/app/data/ndtp.csv")
NDTP_CSV_INTERVAL = int(os.getenv("NDTP_CSV_INTERVAL", "60"))