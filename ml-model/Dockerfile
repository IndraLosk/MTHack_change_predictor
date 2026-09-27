FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY features.py model_service.py model.txt map_matching.py worker.py ./

ENV MODEL_PATH=/app/model.txt

CMD ["python", "worker.py"]