"""Extension (prórroga) validator: approve within the rule set's limits or escalate.

Rules
-----
* The extension is measured in days from the due date of the charge to the requested date.
* A requested date on or before the due date is not an extension (``not_needed``) and does
  not use the yearly quota.
* ``within_limit`` when the days are within ``max_extension_days`` and fewer than
  ``max_extensions_per_year`` extensions were granted in the same calendar year (of the day of
  the request, in Santo Domingo). Otherwise ``escalate``: the owner decides.
* A requested date already in the past cannot be granted automatically: ``escalate``.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class ExtensionOutcome(StrEnum):
    NOT_NEEDED = "not_needed"
    WITHIN_LIMIT = "within_limit"
    ESCALATE = "escalate"


class ExtensionIssue(StrEnum):
    TOO_MANY_DAYS = "too_many_days"
    YEARLY_LIMIT_REACHED = "yearly_limit_reached"
    DATE_IN_PAST = "date_in_past"


@dataclass(frozen=True)
class ExtensionDecision:
    outcome: ExtensionOutcome
    days: int
    used_this_year: int
    issues: tuple[ExtensionIssue, ...] = ()


def review_extension(
    *,
    due_on: dt.date,
    requested_date: dt.date,
    today: dt.date,
    granted_on: Iterable[dt.date],
    max_days: int,
    max_per_year: int,
) -> ExtensionDecision:
    """``granted_on``: the days previous extensions of this lease were granted."""
    days = (requested_date - due_on).days
    used = sum(1 for day in granted_on if day.year == today.year)
    if days <= 0:
        return ExtensionDecision(ExtensionOutcome.NOT_NEEDED, 0, used)

    issues: list[ExtensionIssue] = []
    if requested_date < today:
        issues.append(ExtensionIssue.DATE_IN_PAST)
    if days > max_days:
        issues.append(ExtensionIssue.TOO_MANY_DAYS)
    if used >= max_per_year:
        issues.append(ExtensionIssue.YEARLY_LIMIT_REACHED)
    outcome = ExtensionOutcome.ESCALATE if issues else ExtensionOutcome.WITHIN_LIMIT
    return ExtensionDecision(outcome, days, used, tuple(issues))
