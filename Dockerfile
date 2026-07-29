FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VOSK_MODEL_PATH=/app/models/vosk-model-small-en-us-0.15

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5000} --threads 4 --timeout ${GUNICORN_TIMEOUT:-300} --access-logfile - --error-logfile - run:app"]
