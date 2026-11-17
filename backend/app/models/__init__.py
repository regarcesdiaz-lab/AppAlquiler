"""All SQLAlchemy models. Importing this package registers them on Base.metadata."""

from app.models import guards
from app.models.account import DeviceToken, Organization, User, UserSession
from app.models.activity import Document, InboxItem, Issue, TimelineEvent
from app.models.leasing import ConsentRecord, Lease, RuleSet
from app.models.messaging import AssistantProfile, Message
from app.models.money import BankAccount, LedgerEntry, Payment, PaymentPromise, Receipt
from app.models.property import Building, Tenant, Unit

__all__ = [
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
    "Message",
    "Organization",
    "Payment",
    "PaymentPromise",
    "Receipt",
    "RuleSet",
    "Tenant",
    "TimelineEvent",
    "Unit",
    "User",
    "UserSession",
    "guards",
]
