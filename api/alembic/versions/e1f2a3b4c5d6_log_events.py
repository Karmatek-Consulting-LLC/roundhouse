"""log_events

Append-only platform log stream partitioned by `context` (first context:
"auth" — login/SSO/logout activity). Powers the superadmin Logs console
(list/search/SSE stream/export).

Revision ID: e1f2a3b4c5d6
Revises: c3f2a1e9d4b6
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'c3f2a1e9d4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'log_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('context', sa.String(length=32), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('outcome', sa.String(length=16), nullable=False),
        sa.Column('actor_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column('actor_email', sa.String(length=255), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'log_events_context_ts_index', 'log_events', ['context', 'ts'], unique=False
    )
    op.create_index('log_events_ts_index', 'log_events', ['ts'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('log_events_ts_index', table_name='log_events')
    op.drop_index('log_events_context_ts_index', table_name='log_events')
    op.drop_table('log_events')
