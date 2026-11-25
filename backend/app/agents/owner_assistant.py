"""Owner assistant (phase 8): the owner handles pending items over WhatsApp.

The inbound router already identifies the owner by ``users.whatsapp_phone`` and stores the
message in their organization. Until phase 8 this hook answers nothing, and the router sends
the temporary reply that points to the app and the web panel.
"""

from sqlalchemy.orm import Session

from app.models import Message


def handle(db: Session, message: Message) -> bool:
    """Return True when the assistant answered the owner's message."""
    return False
