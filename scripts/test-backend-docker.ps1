# Default way to test the backend: runs ruff and pytest inside the `api` image (Linux, exact
# versions from uv.lock), against the docker-compose Postgres and Redis. Same environment as CI.
#
#   .\scripts\test-backend-docker.ps1                  # lint + format check + all tests
#   .\scripts\test-backend-docker.ps1 -k health        # only pytest, with these arguments
#   .\scripts\test-backend-docker.ps1 tests/test_jobs.py -x
#
# (No $ErrorActionPreference = "Stop": docker writes progress to stderr; we check the exit code.)

$root = Split-Path -Parent $PSScriptRoot
$compose = @("compose", "--project-directory", $root)

if ($args.Count -gt 0) {
  # Arguments are passed straight to pytest as separate words (no shell in between).
  docker @compose run --rm api uv run --locked pytest @args
} else {
  docker @compose run --rm api sh -c "uv sync --locked && ruff check . && ruff format --check . && pytest"
}
if ($LASTEXITCODE -ne 0) { throw "Backend checks failed (exit code $LASTEXITCODE)" }
