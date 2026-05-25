FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY homesolar ./homesolar
COPY alembic.ini ./
COPY alembic ./alembic

RUN python -m pip install --upgrade pip \
    && python -m pip install .

COPY config/example.yaml ./config/example.yaml


FROM base AS test

COPY tests ./tests
COPY remote_inverter_scrap.html ./remote_inverter_scrap.html

RUN python -m pip install ".[dev]"


FROM base AS production

EXPOSE 8000

CMD ["homesolar", "--config", "/config/homesolar.yaml"]
