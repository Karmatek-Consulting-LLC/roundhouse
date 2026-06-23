"""entra_sso

Entra ID SSO (Phase 1, dashboard). Adds the columns and table the OIDC login
flow needs:

  - users.password_hash made nullable (Entra-only users have no local password).
  - users.auth_source ("local" | "entra") — local users are break-glass and
    exempt from SSO sync.
  - users.oidc_sub — stable Entra subject, the match key across logins.
  - role_mappings — UI-editable Entra app role -> Roundhouse role/team grant,
    read by the claim->grant engine. See docs/entra-sso-plan.md.

Revision ID: c3f2a1e9d4b6
Revises: c3d2f1a8e9b4
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3f2a1e9d4b6'
# Chained after the rc.2 servers/assets migration (merged in) so the platform
# has a single linear head; the lab already at c3d2f1a8e9b4 just applies this.
down_revision: Union[str, Sequence[str], None] = 'c3d2f1a8e9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing rows are all local users with a password set; backfill the new
    # auth_source with a server_default so the NOT NULL add succeeds, then the
    # ORM default ("local") governs future inserts.
    op.add_column(
        'users',
        sa.Column('auth_source', sa.String(length=20), server_default='local', nullable=False),
    )
    op.add_column('users', sa.Column('oidc_sub', sa.String(length=255), nullable=True))
    op.create_unique_constraint('users_oidc_sub_unique', 'users', ['oidc_sub'])
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=True)

    op.create_table(
        'role_mappings',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('entra_app_role', sa.String(length=255), nullable=False),
        sa.Column('roundhouse_role', sa.String(length=20), nullable=False),
        sa.Column('team_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column('team_role', sa.String(length=20), server_default='member', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entra_app_role', name='role_mappings_entra_app_role_unique'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('role_mappings')
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=False)
    op.drop_constraint('users_oidc_sub_unique', 'users', type_='unique')
    op.drop_column('users', 'oidc_sub')
    op.drop_column('users', 'auth_source')
