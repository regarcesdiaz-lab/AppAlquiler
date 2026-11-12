# Runs the backend checks (uv sync --locked, ruff, pytest, OpenAPI export) inside a Linux
# container, against the docker-compose Postgres/Redis. Same environment as CI.
# Useful when Windows Smart App Control blocks compiled Python modules (SQLAlchemy, psycopg).
# Prerequisite: docker compose up -d
# (No $ErrorActionPreference = "Stop": docker writes progress to stderr; we check the exit code.)

$root = Split-Path -Parent $PSScriptRoot
$network = "alquilerapp_default"
$creds = "alquilerapp:alquilerapp"

docker run --rm `
  --network $network `
  -v "${root}\backend:/app" `
  -w /app `
  -e UV_PROJECT_ENVIRONMENT=/opt/venv `
  -e UV_LINK_MODE=copy `
  -e APP_ENV=test `
  -e "DATABASE_URL=postgresql+psycopg://${creds}@postgres:5432/alquilerapp" `
  -e "TEST_DATABASE_URL=postgresql+psycopg://${creds}@postgres:5432/alquilerapp_test" `
  -e REDIS_URL=redis://redis:6379/0 `
  -e MESSAGING_MODE=simulator `
  python:3.12-slim `
  sh -c "pip install -q --root-user-action=ignore uv==0.13.8 && uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run pytest && uv run python scripts/export_openapi.py"
if ($LASTEXITCODE -ne 0) { throw "Backend checks failed" }
