"""oauth_as

OAuth 2.1 authorization server for the MCP data path (Phase 2). Adds the
tables the AS needs; token *validation* in spawned servers is stateless (JWKS),
so nothing here sits on the hot path. See docs/mcp-auth-id-jag.md §7/§10.

  - oauth_clients        — registered clients (manual / DCR / CIMD).
  - oauth_auth_codes     — one-time authorization codes (hashes only).
  - oauth_refresh_tokens — rotating refresh tokens (hashes only).
  - oauth_consents       — remembered per-(user, client) consent.
  - oauth_used_jtis      — single-use assertion ids (ID-JAG profile only).

Revision ID: f2b8c4d7a1e0
Revises: e1f2a3b4c5d6
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f2b8c4d7a1e0'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'oauth_clients',
        sa.Column('client_id', sa.String(length=512), nullable=False),
        sa.Column('client_secret_hash', sa.String(length=64), nullable=True),
        sa.Column('client_name', sa.String(length=255), server_default='', nullable=False),
        sa.Column('token_endpoint_auth_method', sa.String(length=32),
                  server_default='none', nullable=False),
        sa.Column('redirect_uris', sa.JSON(), nullable=True),
        sa.Column('grant_types', sa.JSON(), nullable=True),
        sa.Column('registration_type', sa.String(length=16),
                  server_default='dcr', nullable=False),
        sa.Column('trusted', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('client_id'),
    )

    op.create_table(
        'oauth_auth_codes',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('client_id', sa.String(length=512), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('resource', sa.String(length=1024), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('code_challenge', sa.String(length=128), nullable=False),
        sa.Column('code_challenge_method', sa.String(length=8),
                  server_default='S256', nullable=False),
        sa.Column('redirect_uri', sa.String(length=1024), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code_hash', name='oauth_auth_codes_code_hash_key'),
    )
    op.create_index('oauth_auth_codes_expires_index', 'oauth_auth_codes', ['expires_at'])

    op.create_table(
        'oauth_refresh_tokens',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('client_id', sa.String(length=512), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('resource', sa.String(length=1024), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('replaced_by', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='oauth_refresh_tokens_token_hash_key'),
    )
    op.create_index(
        'oauth_refresh_tokens_user_client_index',
        'oauth_refresh_tokens', ['user_id', 'client_id'],
    )
    op.create_index(
        'oauth_refresh_tokens_expires_index', 'oauth_refresh_tokens', ['expires_at']
    )

    op.create_table(
        'oauth_consents',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('client_id', sa.String(length=512), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'client_id', name='oauth_consents_user_client_uq'),
    )

    op.create_table(
        'oauth_used_jtis',
        sa.Column('jti', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('jti'),
    )
    op.create_index('oauth_used_jtis_expires_index', 'oauth_used_jtis', ['expires_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('oauth_used_jtis')
    op.drop_table('oauth_consents')
    op.drop_table('oauth_refresh_tokens')
    op.drop_table('oauth_auth_codes')
    op.drop_table('oauth_clients')
