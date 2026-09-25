import os

NDTP_EMULATOR_URL = os.getenv("NDTP_EMULATOR_URL", "http://localhost:18080")
NDTP_RECEIVE_PORT = int(os.getenv("NDTP_RECEIVE_PORT", "9201"))