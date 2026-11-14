# Development image for the API and the RQ worker.
# Dependencies come from uv.lock and live in /opt/venv (outside /app), so docker-compose can
# bind-mount the source code at /app without hiding them.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# --locked: install exactly what uv.lock pins; fail if it is out of date with pyproject.toml.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
