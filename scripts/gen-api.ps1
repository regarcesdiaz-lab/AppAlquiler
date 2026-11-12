# Regenerates the typed API client used by the mobile app:
#   1. backend/openapi.json      (from FastAPI, without starting the server)
#   2. mobile/src/api/schema.d.ts (openapi-typescript)
# Run from anywhere:  .\scripts\gen-api.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root "backend\.venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

& $python (Join-Path $root "backend\scripts\export_openapi.py")
if ($LASTEXITCODE -ne 0) { throw "OpenAPI export failed" }

npm --prefix (Join-Path $root "mobile") run gen:api
if ($LASTEXITCODE -ne 0) { throw "TypeScript client generation failed" }

Write-Host "API client up to date. Commit backend/openapi.json and mobile/src/api/schema.d.ts."
