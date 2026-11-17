"""Account deletion (App Store / Google Play requirement, CLAUDE.md rule 13)."""

import uuid

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.models import Organization


def purge_organization(db: Session, organization_id: uuid.UUID) -> None:
    """Permanently delete an organization and every row that belongs to it.

    Rows cascade from the organization. Append-only tables (timeline, ledger) only accept
    DELETE while app.allow_account_purge is 'on'; the flag is local to this transaction and
    switched off again right after the delete. The caller commits.
    """
    # If the DELETE fails the transaction is aborted, and the local setting dies with it.
    db.execute(text("SELECT set_config('app.allow_account_purge', 'on', true)"))
    db.execute(delete(Organization).where(Organization.id == organization_id))
    db.execute(text("SELECT set_config('app.allow_account_purge', 'off', true)"))
    db.expire_all()
