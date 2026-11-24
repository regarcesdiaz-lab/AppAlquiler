"""WhatsApp channel (central number) behind an interface.

This phase only has the local simulator: nothing leaves the machine. The real Meta WhatsApp
Cloud API client arrives with the agents phase; with MESSAGING_MODE=live and no real client the
channel refuses to send instead of pretending (CLAUDE.md, rule 10).
"""

import logging
import uuid
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class WhatsAppChannel(Protocol):
    def send(self, *, to: str, body: str, template: str | None) -> str:
        """Send a message and return the provider's message id."""
        ...


class SimulatorWhatsApp:
    def send(self, *, to: str, body: str, template: str | None) -> str:
        provider_id = f"sim.{uuid.uuid4().hex}"
        logger.info(
            "WHATSAPP (simulator) to=%s template=%s id=%s\n%s", to, template, provider_id, body
        )
        return provider_id


class LiveWhatsAppNotAvailableError(RuntimeError):
    pass


def get_whatsapp_channel() -> WhatsAppChannel:
    if get_settings().MESSAGING_MODE == "simulator":
        return SimulatorWhatsApp()
    raise LiveWhatsAppNotAvailableError(
        "MESSAGING_MODE=live but the WhatsApp Cloud API client is not implemented yet"
    )
