# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# Multi-stage build: compile wheels with heavy toolchains in a throw-away stage,
# then ship a slim runtime image. This typically cuts the final image from
# ~2.5 GB to ~600-800 MB, which means:
#   * faster ECR pulls / ECS task starts
#   * smaller EBS footprint per instance
#   * lower memory resident set for each container (backend / worker / beat)
# -----------------------------------------------------------------------------

FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        g++ \
        gcc \
        python3-dev \
        libsndfile1-dev \
        portaudio19-dev \
        libboost-all-dev \
        binutils \
        libproj-dev \
        libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip wheel --wheel-dir=/wheels -r requirements.txt


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=mizan.settings

WORKDIR /app

# Only runtime libs live in the final image — no compilers, no -dev packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        gdal-bin \
        libgdal32 \
        libproj25 \
        libsndfile1 \
        netcat-openbsd \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

# Copy source + run collectstatic at build time, NOT on every container start.
COPY . .
RUN python manage.py collectstatic --noinput || true

EXPOSE 8000

# Default command runs Daphne. docker-compose overrides this for worker/beat.
CMD ["sh", "-c", "until nc -z ${POSTGRES_HOST:-db} ${POSTGRES_PORT:-5432}; do echo 'Waiting for database...'; sleep 2; done && \
    python manage.py migrate --noinput && \
    daphne -b 0.0.0.0 -p 8000 -v 1 mizan.asgi:application"]
