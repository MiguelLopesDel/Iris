# One image definition for both runtime profiles:
#   docker build .                             -> CPU image
#   docker build --build-arg IRIS_PROFILE=cuda . -> NVIDIA image (CUDA 13 from pip wheels;
#                                                 the host only needs driver >= 580)
FROM python:3.13-slim

ARG IRIS_PROFILE=cpu
# The published image runs as 1000:1000 by default; docker-compose.yml overrides
# the user at run time with IRIS_UID/IRIS_GID so bind-mounted data/ stays owned
# by the host account.
ARG IRIS_UID=1000
ARG IRIS_GID=1000
# Recorded in every backup, so a backup says which Iris wrote it.
ARG IRIS_COMMIT=""
WORKDIR /app

# ffmpeg: video/audio decoding. libchromaprint-tools: fpcalc for audio
# duplicate detection. libcairo2: SVG rasterisation through cairosvg.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libchromaprint-tools \
    libcairo2 \
    libglib2.0-0 \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-cuda.txt scripts/check_deps.py ./

# The locks pin the full graph with hashes and deliberately omit packages that
# would shadow opencv-python-headless / onnxruntime-gpu, hence --no-deps.
RUN case "$IRIS_PROFILE" in \
        cpu) lock=requirements.txt; check="" ;; \
        cuda) lock=requirements-cuda.txt; check="--cuda" ;; \
        *) echo "IRIS_PROFILE must be cpu or cuda" >&2; exit 1 ;; \
    esac \
    && pip install --no-cache-dir --no-deps --require-hashes -r "$lock" \
    && python check_deps.py $check \
    && rm check_deps.py

RUN groupadd --gid "$IRIS_GID" iris \
    && useradd --uid "$IRIS_UID" --gid iris --create-home --shell /usr/sbin/nologin iris

COPY --chown=iris:iris . .

# Model caches are named volumes. They are created world-writable so a container
# started with another --user can still download models into them.
RUN mkdir -p data media \
        /home/iris/.cache/huggingface /home/iris/.cache/whisper \
        /home/iris/.insightface /home/iris/.EasyOCR \
    && chown -R iris:iris data media /home/iris \
    && chmod 1777 /home/iris /home/iris/.cache /home/iris/.cache/huggingface \
        /home/iris/.cache/whisper /home/iris/.insightface /home/iris/.EasyOCR

# 8501: HTTP for browsers. 8443: HTTPS for devices, when IRIS_TLS is self or custom.
EXPOSE 8501 8443

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -fsS http://localhost:8501/healthz || exit 1

ENV PYTHONPATH=/app \
    IRIS_COMMIT=${IRIS_COMMIT} \
    HOME=/home/iris \
    HF_HOME=/home/iris/.cache/huggingface \
    XDG_CACHE_HOME=/home/iris/.cache \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

USER iris

# HTTP for browsers, plus HTTPS for devices when IRIS_TLS asks for it (scripts/serve.py).
CMD ["python3", "scripts/serve.py"]
