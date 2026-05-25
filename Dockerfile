FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY homesolar ./homesolar

RUN python -m pip install --upgrade pip \
    && python -m pip install .

COPY config/example.yaml ./config/example.yaml

EXPOSE 8000

CMD ["homesolar", "--config", "/config/homesolar.yaml"]
