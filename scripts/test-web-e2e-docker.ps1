# Runs the site's Playwright tests inside the official Playwright Linux image, against the static
# export in web/out. Needed on Windows because Smart App Control blocks Playwright's Chromium.
# Prerequisite: npm --prefix web run build
#
# (No $ErrorActionPreference = "Stop": docker writes progress to stderr; we check the exit code.)

$root = Split-Path -Parent $PSScriptRoot
$web = Join-Path $root "web"

if (-not (Test-Path (Join-Path $web "out\index.html"))) {
  throw "web/out not found. Run 'npm run build' in web/ first."
}

# The image tag must match the installed @playwright/test version.
$version = (Get-Content (Join-Path $web "node_modules\@playwright\test\package.json") -Raw | ConvertFrom-Json).version

# web/ is mounted read-only; the test files are copied to /work so Linux node_modules never
# mix with the Windows ones.
docker run --rm -v "${web}:/web:ro" -w /work -e CI=1 "mcr.microsoft.com/playwright:v$version-noble" `
  sh -c "cp -r /web/out /web/e2e /web/scripts /web/playwright.config.ts /web/package.json . && npm install --silent --no-save --no-package-lock @playwright/test@$version && npx playwright test"
if ($LASTEXITCODE -ne 0) { throw "Playwright tests failed (exit code $LASTEXITCODE)" }
