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

## Deployment with GitHub Actions

The repository includes a workflow at `.github/workflows/deploy.yml`.

It expects a self-hosted GitHub Actions runner on the Raspberry Pi with the custom label
`homesolar-pi`. The deploy job runs on that runner, builds the Docker image locally, and restarts
the Compose service.

One-time setup on the Pi:

```bash
sudo mkdir -p /opt/homesolar/config /opt/homesolar/data
sudo chown -R "$USER":"$USER" /opt/homesolar
cp config/example.yaml /opt/homesolar/config/local.yaml
```

Edit `/opt/homesolar/config/local.yaml` on the Pi with the real inverter URLs and polling intervals.

Add these GitHub repository secrets:

```text
REMOTE_INVERTER_USER
REMOTE_INVERTER_PASSWORD
```

The workflow uses these persistent host paths:

```text
HOMESOLAR_CONFIG=/opt/homesolar/config/local.yaml
HOMESOLAR_DATA_DIR=/opt/homesolar/data
```

Pushes to `main` run tests and lint first, then deploy. You can also run the workflow manually from
GitHub Actions.

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
