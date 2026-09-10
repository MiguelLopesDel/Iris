FROM python:3.12-slim

ARG IRIS_UID=1000
ARG IRIS_GID=1000
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    g++ \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid "$IRIS_GID" iris \
    && useradd --uid "$IRIS_UID" --gid iris --create-home --shell /usr/sbin/nologin iris

COPY --chown=iris:iris . .

RUN mkdir -p data media /home/iris/.cache/huggingface /home/iris/.cache/whisper \
    && chown -R iris:iris data media /home/iris

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8501/healthz || exit 1

ENV PYTHONPATH=/app \
    HF_HOME=/home/iris/.cache/huggingface \
    XDG_CACHE_HOME=/home/iris/.cache

USER iris

CMD ["python3", "-m", "uvicorn", "server:app", \
     "--host=0.0.0.0", \
     "--port=8501"]
