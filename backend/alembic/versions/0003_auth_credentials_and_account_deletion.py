"""auth credentials (passwords, refresh tokens, magic links) and account deletion log

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23 16:19:52.444092

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('account_deletion_requests',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('email_sha256', sa.String(length=64), nullable=False),
    sa.Column('client', sa.Enum('app', 'web', name='client_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_account_deletion_requests'))
    )
    op.create_index('ix_account_deletion_requests_organization_id', 'account_deletion_requests', ['organization_id'], unique=False)
    op.create_table('magic_link_tokens',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('client', sa.Enum('app', 'web', name='client_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_magic_link_tokens_expires_after_creation')),
    sa.ForeignKeyConstraint(['organization_id', 'user_id'], ['users.organization_id', 'users.id'], name=op.f('fk_magic_link_tokens_organization_id_user_id'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_magic_link_tokens_organization_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_magic_link_tokens')),
    sa.UniqueConstraint('organization_id', 'id', name=op.f('uq_magic_link_tokens_organization_id_id')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_magic_link_tokens_token_hash'))
    )
    op.create_index('ix_magic_link_tokens_user_id', 'magic_link_tokens', ['user_id'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('family_id', sa.Uuid(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('auth_method', sa.Enum('password', 'magic_link', name='auth_method', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('authenticated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('device_label', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_refresh_tokens_expires_after_creation')),
    sa.ForeignKeyConstraint(['organization_id', 'user_id'], ['users.organization_id', 'users.id'], name=op.f('fk_refresh_tokens_organization_id_user_id'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_refresh_tokens_organization_id'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('organization_id', 'id', name=op.f('uq_refresh_tokens_organization_id_id')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_refresh_tokens_token_hash'))
    )
    op.create_index('ix_refresh_tokens_family_id', 'refresh_tokens', ['family_id'], unique=False)
    op.create_index('ix_refresh_tokens_user_active', 'refresh_tokens', ['user_id'], unique=False, postgresql_where=sa.text('revoked_at IS NULL'))
    op.add_column('user_sessions', sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('user_sessions', sa.Column('auth_method', sa.String(length=32), server_default='password', nullable=False))
    op.create_check_constraint(op.f('ck_user_sessions_auth_method'), 'user_sessions', "auth_method IN ('password', 'magic_link')")
    op.add_column('user_sessions', sa.Column('authenticated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('tokens_valid_after', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('terms_accepted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'terms_accepted_at')
    op.drop_column('users', 'tokens_valid_after')
    op.drop_column('users', 'password_hash')
    op.drop_column('user_sessions', 'authenticated_at')
    op.drop_constraint(op.f('ck_user_sessions_auth_method'), 'user_sessions', type_='check')
    op.drop_column('user_sessions', 'auth_method')
    op.drop_column('user_sessions', 'last_seen_at')
    op.drop_index('ix_refresh_tokens_user_active', table_name='refresh_tokens', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_index('ix_refresh_tokens_family_id', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_magic_link_tokens_user_id', table_name='magic_link_tokens')
    op.drop_table('magic_link_tokens')
    op.drop_index('ix_account_deletion_requests_organization_id', table_name='account_deletion_requests')
    op.drop_table('account_deletion_requests')
