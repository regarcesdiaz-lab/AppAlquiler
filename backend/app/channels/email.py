"""Transactional email behind an interface.

Only the ``console`` provider exists in this phase: it writes the email to the log so magic
links can be followed in development. Real providers (Resend, Postmark, SES) plug in here later;
asking for one that is not implemented fails loudly instead of silently dropping the email.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str


class EmailSender(Protocol):
    def send(self, email: Email) -> None: ...


class ConsoleEmailSender:
    """Development only: prints the email (including any link) to the API log."""

    def send(self, email: Email) -> None:
        logger.warning("EMAIL (console) to=%s subject=%s\n%s", email.to, email.subject, email.text)


def get_email_sender() -> EmailSender:
    settings = get_settings()
    if settings.EMAIL_PROVIDER == "console":
        if settings.is_production:
            raise RuntimeError("EMAIL_PROVIDER=console is not allowed in production")
        return ConsoleEmailSender()
    raise NotImplementedError(f"Email provider {settings.EMAIL_PROVIDER!r} is not implemented yet")
