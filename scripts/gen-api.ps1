# Regenerates the typed API client used by the app:
#   1. backend/openapi.json       (from FastAPI, inside the `api` Docker image; no server needed)
#   2. mobile/src/api/schema.d.ts (openapi-typescript, on the host)
# Run from anywhere:  .\scripts\gen-api.ps1
#
# (No $ErrorActionPreference = "Stop": docker writes progress to stderr; we check the exit code.)

$root = Split-Path -Parent $PSScriptRoot

docker compose --project-directory $root run --rm --no-deps api uv run --locked python scripts/export_openapi.py
if ($LASTEXITCODE -ne 0) { throw "OpenAPI export failed" }

npm --prefix (Join-Path $root "mobile") run gen:api
if ($LASTEXITCODE -ne 0) { throw "TypeScript client generation failed" }

Write-Host "API client up to date. Commit backend/openapi.json and mobile/src/api/schema.d.ts."
