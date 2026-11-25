"""Local WhatsApp simulator (development and tests): nothing leaves the machine.

Outbound messages are kept per phone number in Redis instead of going to Meta, so the
simulator page (/dev/whatsapp) can show them as a chat. Files "sent" from the page are kept in
Redis too and served back through ``fetch_media``, exactly like Meta's media download.
"""

import datetime as dt
import json
import logging
import uuid
from typing import Any

from redis import Redis

from app.channels.whatsapp.base import (
    MAX_MEDIA_BYTES,
    MediaFile,
    MediaTooLargeError,
    OutboundContent,
    WhatsAppError,
    content_to_json,
)

logger = logging.getLogger(__name__)

DEFAULT_PREFIX = "whatsapp-sim"
MEDIA_TTL_SECONDS = 24 * 3600
MAX_ENTRIES = 500


def new_provider_id() -> str:
    """Looks like a Meta wamid, and says it comes from the simulator."""
    return f"wamid.SIM{uuid.uuid4().hex.upper()}"


class SimulatorChannel:
    def __init__(self, redis: Redis, prefix: str = DEFAULT_PREFIX) -> None:
        self.redis = redis
        self.prefix = prefix

    # -- keys --------------------------------------------------------------------------------

    def _chat_key(self, phone: str) -> str:
        return f"{self.prefix}:chat:{phone}"

    def _media_key(self, media_id: str) -> str:
        return f"{self.prefix}:media:{media_id}"

    @property
    def _phones_key(self) -> str:
        return f"{self.prefix}:phones"

    # -- channel interface -------------------------------------------------------------------

    def send(self, to: str, content: OutboundContent) -> str:
        provider_id = new_provider_id()
        self._append(
            to,
            {
                "id": provider_id,
                "direction": "out",
                "at": dt.datetime.now(dt.UTC).isoformat(),
                "content": content_to_json(content),
                "status": "sent",
            },
        )
        logger.info("WHATSAPP (simulator) to=%s type=%s id=%s", to, content.type, provider_id)
        return provider_id

    def fetch_media(self, media_id: str) -> MediaFile:
        data = self.redis.hgetall(self._media_key(media_id))
        if not data:
            raise WhatsAppError(f"Unknown simulator media {media_id}")
        content = data[b"content"]
        if len(content) > MAX_MEDIA_BYTES:
            raise MediaTooLargeError(f"Media is {len(content)} bytes")
        filename = data.get(b"filename", b"").decode() or None
        return MediaFile(content=content, mime=data[b"mime"].decode(), filename=filename)

    # -- simulator page helpers --------------------------------------------------------------

    def store_media(self, content: bytes, mime: str, filename: str | None) -> str:
        media_id = f"sim-media-{uuid.uuid4().hex}"
        key = self._media_key(media_id)
        self.redis.hset(key, mapping={"content": content, "mime": mime, "filename": filename or ""})
        self.redis.expire(key, MEDIA_TTL_SECONDS)
        return media_id

    def record_inbound(self, phone: str, provider_id: str, summary: dict[str, Any]) -> None:
        self._append(
            phone,
            {
                "id": provider_id,
                "direction": "in",
                "at": dt.datetime.now(dt.UTC).isoformat(),
                "content": summary,
                "status": "received",
            },
        )

    def chat(self, phone: str) -> list[dict[str, Any]]:
        return [json.loads(raw) for raw in self.redis.lrange(self._chat_key(phone), 0, -1)]

    def set_status(self, phone: str, provider_id: str, status: str) -> None:
        key = self._chat_key(phone)
        for index, raw in enumerate(self.redis.lrange(key, 0, -1)):
            entry = json.loads(raw)
            if entry["id"] == provider_id:
                entry["status"] = status
                self.redis.lset(key, index, json.dumps(entry))
                return

    def phones(self) -> list[str]:
        return sorted(p.decode() for p in self.redis.smembers(self._phones_key))

    def clear(self, phone: str) -> None:
        self.redis.delete(self._chat_key(phone))
        self.redis.srem(self._phones_key, phone)

    def _append(self, phone: str, entry: dict[str, Any]) -> None:
        key = self._chat_key(phone)
        self.redis.rpush(key, json.dumps(entry, ensure_ascii=False))
        self.redis.ltrim(key, -MAX_ENTRIES, -1)
        self.redis.sadd(self._phones_key, phone)
