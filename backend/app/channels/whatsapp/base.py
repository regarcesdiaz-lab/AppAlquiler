"""WhatsApp channel interface and the provider-neutral shapes that cross it.

Inbound: the webhook (Meta) and the local simulator both produce Meta's webhook JSON, which
``parser.parse_webhook`` turns into ``InboundEvent`` and ``StatusUpdate``. Outbound: services
build an ``OutboundContent`` and the channel delivers it. Nothing above this module knows if the
message went to Meta or to the simulator.
"""

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

InboundType = Literal[
    "text",
    "button",  # quick-reply button of a template
    "interactive",  # reply button or list row of a session message
    "image",
    "document",
    "audio",
    "video",
    "sticker",
    "location",
    "reaction",
    "other",
]


class InboundEvent(BaseModel):
    """One inbound message, normalized. Stored as JSON in whatsapp_inbound_events."""

    model_config = ConfigDict(frozen=True)

    provider_message_id: str
    phone: str  # E.164
    timestamp: dt.datetime
    type: InboundType
    text: str | None = None
    # Button / list payload ("v1:<action>:<id>") and the title the person tapped.
    payload: str | None = None
    media_id: str | None = None
    media_mime: str | None = None  # as declared by the sender
    media_filename: str | None = None
    caption: str | None = None
    context_id: str | None = None  # provider id of the message this one answers


DeliveryStatus = Literal["sent", "delivered", "read", "failed"]


@dataclass(frozen=True)
class StatusUpdate:
    provider_message_id: str
    status: DeliveryStatus
    timestamp: dt.datetime
    error_code: int | None = None
    error_title: str | None = None


# -- outbound ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Button:
    id: str  # payload, "v1:<action>:<lease_id>"
    title: str


@dataclass(frozen=True)
class ListRowOut:
    id: str
    title: str
    description: str | None = None


@dataclass(frozen=True)
class TemplateSend:
    name: str  # approved name, e.g. "aviso_amable_tu"
    language: str
    params: dict[str, str]
    # One payload per quick-reply button, in the template's order; titles only for display.
    buttons: tuple[Button, ...] = ()
    footer: str | None = None
    header_document_id: str | None = None
    header_document_filename: str | None = None
    preview: str = ""  # rendered body, for logs and the simulator
    type: Literal["template"] = "template"


@dataclass(frozen=True)
class TextSend:
    body: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ButtonsSend:
    body: str
    buttons: tuple[Button, ...]
    footer: str | None = None
    type: Literal["buttons"] = "buttons"


@dataclass(frozen=True)
class ListSend:
    body: str
    button: str
    rows: tuple[ListRowOut, ...]
    footer: str | None = None
    type: Literal["list"] = "list"


OutboundContent = TemplateSend | TextSend | ButtonsSend | ListSend
SessionContent = TextSend | ButtonsSend | ListSend


def content_to_json(content: OutboundContent) -> dict[str, Any]:
    return asdict(content)


def content_from_json(data: dict[str, Any]) -> OutboundContent:
    kind = data.get("type")
    if kind == "template":
        return TemplateSend(
            **{
                **data,
                "buttons": tuple(Button(**b) for b in data.get("buttons", ())),
            }
        )
    if kind == "text":
        return TextSend(**data)
    if kind == "buttons":
        return ButtonsSend(**{**data, "buttons": tuple(Button(**b) for b in data["buttons"])})
    if kind == "list":
        return ListSend(**{**data, "rows": tuple(ListRowOut(**r) for r in data["rows"])})
    raise ValueError(f"Unknown outbound content type {kind!r}")


@dataclass(frozen=True)
class MediaFile:
    content: bytes
    mime: str  # as reported by the provider
    filename: str | None = None


class WhatsAppError(RuntimeError):
    """The provider refused or could not be reached."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class MediaTooLargeError(WhatsAppError):
    pass


class WhatsAppChannel(Protocol):
    def send(self, to: str, content: OutboundContent) -> str:
        """Send and return the provider's message id."""
        ...

    def fetch_media(self, media_id: str) -> MediaFile:
        """Download an inbound image or document."""
        ...


MAX_MEDIA_BYTES = 10 * 1024 * 1024
