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

All jobs run on GitHub self-hosted runners. The workflow then SSHes from the runner into the
Raspberry Pi, builds both Docker images on the Pi, tests the test image, runs migrations, deploys
the production image, and removes old images while keeping the latest 5 production and latest 5
test images.

One-time setup on the Pi:

```bash
mkdir -p /home/alexandros
git clone git@github.com:alexandrosm77/homesolar.git /home/alexandros/homesolar
cd /home/alexandros/homesolar
mkdir -p /home/alexandros/homesolar/config /home/alexandros/homesolar/data
cp config/example.yaml /home/alexandros/homesolar/config/local.yaml
```

Edit `/home/alexandros/homesolar/config/local.yaml` on the Pi with the real inverter URLs and polling
intervals. `config/local.yaml`, `data/`, and `.deploy/` are git-ignored runtime paths inside the app
directory.
The Pi user also needs Docker access and GitHub repository read access, because the workflow runs
`git fetch` from `git@github.com:alexandrosm77/homesolar.git` on the Pi. Add a GitHub deploy key for the
Pi or configure GitHub SSH access for the Pi user.

Add these GitHub repository variables:

```text
PI_USER=alexandros
PI_HOST=192.168.0.11
PI_PORT=22
PI_APP_DIR=/home/alexandros/homesolar
PI_CONFIG_PATH=/home/alexandros/homesolar/config/local.yaml
PI_DATA_DIR=/home/alexandros/homesolar/data
HOMESOLAR_PORT=8080
```

Add these GitHub repository secrets:

```text
PI_SSH_KEY
REMOTE_INVERTER_USER
REMOTE_INVERTER_PASSWORD
```

`PI_SSH_KEY` is the private key used by the self-hosted runner to SSH into the Pi. Its public key
must be present in `/home/alexandros/.ssh/authorized_keys` on the Pi.

The workflow deploys these image tags on the Pi:

```text
homesolar:<git-sha>
homesolar:test-<git-sha>
```

Pushes to `main` run build, test, deploy, and cleanup. You can also run the workflow manually from
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

