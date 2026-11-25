from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import dev_simulator, health, webhooks
from app.api.v1 import account, auth, home, imports, leases, payments, properties, settings
from app.core.config import get_settings

API_PREFIX = "/v1"


def create_app() -> FastAPI:
    config = get_settings()
    docs = not config.is_production  # interactive docs only outside production
    app = FastAPI(
        title="AlquilerApp API",
        version=__version__,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    # Only the web panel's origin may call the API from a browser, with credentials (cookie).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
        max_age=600,
    )
    app.include_router(health.router)
    app.include_router(webhooks.router)
    # Local WhatsApp simulator: development and tests only, never in production.
    if config.simulator_enabled:
        app.include_router(dev_simulator.router)

    v1 = APIRouter(prefix=API_PREFIX)
    for module in (auth, account, settings, properties, leases, imports, home, payments):
        v1.include_router(module.router)
    app.include_router(v1)
    return app


app = create_app()
