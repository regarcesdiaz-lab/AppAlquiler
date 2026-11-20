"""Enumerations stored as text columns with a CHECK constraint (not native PostgreSQL enums)."""

from enum import StrEnum

from app.domain.rule_templates import LateFeeFrequency, LateFeeType, RuleTemplate

__all__ = [
    "AccountType",
    "ActorType",
    "AssistantTone",
    "ConsentMethod",
    "DevicePlatform",
    "DocumentType",
    "InboxItemStatus",
    "InboxItemType",
    "IssueStatus",
    "LateFeeFrequency",
    "LateFeeType",
    "LedgerEntryType",
    "MessageChannel",
    "MessageDirection",
    "MessageStatus",
    "PaymentSource",
    "PaymentStatus",
    "PromiseCreator",
    "PromiseStatus",
    "RuleTemplate",
    "UserRole",
]


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"


class DevicePlatform(StrEnum):
    IOS = "ios"
    ANDROID = "android"


class AccountType(StrEnum):
    SAVINGS = "savings"
    CHECKING = "checking"


class ConsentMethod(StrEnum):
    CONTRACT_CLAUSE = "contract_clause"
    WHATSAPP_REPLY = "whatsapp_reply"


class LedgerEntryType(StrEnum):
    CHARGE = "charge"
    PAYMENT = "payment"
    LATE_FEE = "late_fee"
    ADJUSTMENT = "adjustment"


class PaymentStatus(StrEnum):
    REPORTED = "reported"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class PaymentSource(StrEnum):
    WHATSAPP_RECEIPT = "whatsapp_receipt"
    MANUAL_CASH = "manual_cash"
    CARD_LINK = "card_link"


class PromiseStatus(StrEnum):
    ACTIVE = "active"
    KEPT = "kept"
    BROKEN = "broken"


class PromiseCreator(StrEnum):
    TENANT = "tenant"
    OWNER = "owner"
    AGENT = "agent"


class AssistantTone(StrEnum):
    CORDIAL = "cordial"
    FORMAL = "formal"


class MessageChannel(StrEnum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    PUSH = "push"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    RECEIVED = "received"  # inbound messages only


class ActorType(StrEnum):
    SYSTEM = "system"
    AGENT = "agent"
    OWNER = "owner"
    TENANT = "tenant"


class IssueStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class DocumentType(StrEnum):
    LEASE_CONTRACT = "lease_contract"
    ID_CARD = "id_card"
    OTHER = "other"


class InboxItemType(StrEnum):
    PAYMENT_TO_CONFIRM = "payment_to_confirm"
    PAYMENT_UNMATCHED = "payment_unmatched"  # receipt to an unknown or inactive bank account
    PROMISE_TO_APPROVE = "promise_to_approve"
    PROMISE_BROKEN = "promise_broken"
    ISSUE_REPORTED = "issue_reported"
    TENANT_MESSAGE = "tenant_message"  # the assistant could not answer on its own
    CONSENT_REVOKED = "consent_revoked"
    OVERDUE_ESCALATION = "overdue_escalation"  # still unpaid after the rule set's escalation day


class InboxItemStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
