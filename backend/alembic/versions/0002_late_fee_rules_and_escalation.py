"""late fee frequency and cap, escalation day, reminder limits, late_fee_agreed

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20 21:19:24.103365

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INBOX_TYPES_V1 = (
    'payment_to_confirm', 'payment_unmatched', 'promise_to_approve', 'promise_broken',
    'issue_reported', 'tenant_message', 'consent_revoked',
)
INBOX_TYPES_V2 = (*INBOX_TYPES_V1, 'overdue_escalation')


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.add_column('leases', sa.Column('late_fee_agreed', sa.Boolean(), server_default=sa.text('false'), nullable=False))

    op.add_column('rule_sets', sa.Column('late_fee_frequency', sa.String(length=32), server_default='once', nullable=False))
    op.add_column('rule_sets', sa.Column('late_fee_cap_percent', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column('rule_sets', sa.Column('escalation_days', sa.SmallInteger(), server_default=sa.text('10'), nullable=False))
    op.add_column('rule_sets', sa.Column('max_reminders_per_day', sa.SmallInteger(), server_default=sa.text('1'), nullable=False))
    op.add_column('rule_sets', sa.Column('max_reminders_per_week', sa.SmallInteger(), server_default=sa.text('3'), nullable=False))
    op.create_check_constraint(op.f('ck_rule_sets_late_fee_frequency'), 'rule_sets', "late_fee_frequency IN ('once', 'daily')")
    op.create_check_constraint(op.f('ck_rule_sets_late_fee_cap_range'), 'rule_sets', 'late_fee_cap_percent IS NULL OR (late_fee_cap_percent > 0 AND late_fee_cap_percent <= 10.00)')
    op.create_check_constraint(op.f('ck_rule_sets_escalation_days_range'), 'rule_sets', 'escalation_days BETWEEN 0 AND 90')
    op.create_check_constraint(op.f('ck_rule_sets_max_reminders_per_day_positive'), 'rule_sets', 'max_reminders_per_day >= 1')
    op.create_check_constraint(op.f('ck_rule_sets_max_reminders_per_week_order'), 'rule_sets', 'max_reminders_per_week >= max_reminders_per_day')

    op.drop_constraint(op.f('ck_inbox_items_inbox_item_type'), 'inbox_items', type_='check')
    op.create_check_constraint(op.f('ck_inbox_items_inbox_item_type'), 'inbox_items', f'type IN ({_in_list(INBOX_TYPES_V2)})')


def downgrade() -> None:
    op.execute("DELETE FROM inbox_items WHERE type = 'overdue_escalation'")
    op.drop_constraint(op.f('ck_inbox_items_inbox_item_type'), 'inbox_items', type_='check')
    op.create_check_constraint(op.f('ck_inbox_items_inbox_item_type'), 'inbox_items', f'type IN ({_in_list(INBOX_TYPES_V1)})')

    for name in ('max_reminders_per_week_order', 'max_reminders_per_day_positive', 'escalation_days_range', 'late_fee_cap_range', 'late_fee_frequency'):
        op.drop_constraint(op.f(f'ck_rule_sets_{name}'), 'rule_sets', type_='check')
    op.drop_column('rule_sets', 'max_reminders_per_week')
    op.drop_column('rule_sets', 'max_reminders_per_day')
    op.drop_column('rule_sets', 'escalation_days')
    op.drop_column('rule_sets', 'late_fee_cap_percent')
    op.drop_column('rule_sets', 'late_fee_frequency')
    op.drop_column('leases', 'late_fee_agreed')
