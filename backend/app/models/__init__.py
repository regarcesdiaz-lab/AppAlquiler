"""All SQLAlchemy models. Importing this package registers them on Base.metadata."""

from app.models import guards
from app.models.account import (
    AccountDeletionRequest,
    DeviceToken,
    MagicLinkToken,
    Organization,
    RefreshToken,
    User,
    UserSession,
)
from app.models.activity import Document, InboxItem, Issue, TimelineEvent
from app.models.leasing import ConsentRecord, Lease, RuleSet
from app.models.messaging import (
    AssistantProfile,
    Message,
    WhatsAppConversation,
    WhatsAppInboundEvent,
)
from app.models.money import BankAccount, LedgerEntry, Payment, PaymentPromise, Receipt
from app.models.property import Building, Tenant, Unit

__all__ = [
    "AccountDeletionRequest",
    "AssistantProfile",
    "BankAccount",
    "Building",
    "ConsentRecord",
    "DeviceToken",
    "Document",
    "InboxItem",
    "Issue",
    "Lease",
    "LedgerEntry",
    "MagicLinkToken",
    "Message",
    "Organization",
    "Payment",
    "PaymentPromise",
    "Receipt",
    "RefreshToken",
    "RuleSet",
    "Tenant",
    "TimelineEvent",
    "Unit",
    "User",
    "UserSession",
    "WhatsAppConversation",
    "WhatsAppInboundEvent",
    "guards",
]
