# homesolar

Local solar inverter collector, API, and phone-friendly dashboard.

The app is intentionally boring: one Python/FastAPI process polls configured inverter adapters,
stores normalized readings and raw payloads in SQLite, and serves a small dashboard/API.

## Local development

```bash
pyenv virtualenv 3.12.10 homesolar-3.12
pyenv local homesolar-3.12
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp config/example.yaml config/local.yaml
cp .env.example .env
homesolar --config config/local.yaml
```

Dashboard: <http://127.0.0.1:8000>

## Docker

```bash
cp config/example.yaml config/local.yaml
cp .env.example .env
docker compose up --build
```

SQLite is stored in `./data/homesolar.sqlite` by default.

## Configuration

Credentials are referenced through environment variables so secrets do not need to live in YAML:

```yaml
auth:
  type: basic
  username_env: REMOTE_INVERTER_USER
  password_env: REMOTE_INVERTER_PASSWORD
```

For local runs, values from `.env` are loaded automatically. Docker Compose also reads `.env`.

Polling intervals are per inverter and per task, so remote/VPN-backed sources can be queried less
often than LAN sources.
