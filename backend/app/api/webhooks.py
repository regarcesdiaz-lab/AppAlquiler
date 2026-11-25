"""Meta WhatsApp Cloud API webhook.

* ``GET``: subscription check. Meta sends ``hub.mode=subscribe``, ``hub.verify_token`` and
  ``hub.challenge``; we answer the challenge only when the token matches ours.
* ``POST``: events. The raw body must be signed with the app secret (``X-Hub-Signature-256:
  sha256=<hex HMAC-SHA256>``). Statuses update at once; messages go to the queue, so Meta gets
  its 200 quickly.

Not in the OpenAPI schema: only Meta calls these routes.
"""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, Response

from app.api.deps import DbSession
from app.core.config import get_settings
from app.services.inbound import handle_webhook_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", include_in_schema=False)
SIGNATURE_HEADER = "X-Hub-Signature-256"


def signature_for(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signature_is_valid(body: bytes, header: str | None, secret: str) -> bool:
    if not header or not secret:
        return False
    return hmac.compare_digest(signature_for(body, secret), header.strip())


@router.get("/whatsapp")
def verify_subscription(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    expected = get_settings().WHATSAPP_VERIFY_TOKEN
    if (
        mode == "subscribe"
        and expected
        and token is not None
        and hmac.compare_digest(token.encode(), expected.encode())
        and challenge is not None
    ):
        return PlainTextResponse(challenge)
    return Response(status_code=status.HTTP_403_FORBIDDEN)


def _process(db: DbSession, payload: object) -> None:
    handle_webhook_payload(db, payload)
    db.commit()


@router.post("/whatsapp")
async def receive_events(request: Request, db: DbSession) -> Response:
    secret = get_settings().WHATSAPP_APP_SECRET
    if not secret:
        logger.error("WhatsApp webhook called but WHATSAPP_APP_SECRET is not set")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    body = await request.body()
    if not signature_is_valid(body, request.headers.get(SIGNATURE_HEADER), secret):
        logger.warning("WhatsApp webhook with a missing or invalid signature")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        payload = json.loads(body)
    except ValueError:
        logger.warning("WhatsApp webhook body is not JSON")
        return Response(status_code=status.HTTP_200_OK)
    await run_in_threadpool(_process, db, payload)
    return Response(status_code=status.HTTP_200_OK)
