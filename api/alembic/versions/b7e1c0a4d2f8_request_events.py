"""request_events

Persistent, queryable history of MCP primitive invocations (metadata only),
powering the realtime Observe console. Pushed by each spawned server's
middleware to /api/ingest/events.

Revision ID: b7e1c0a4d2f8
Revises: 2a69b275d501
Create Date: 2026-06-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e1c0a4d2f8'
down_revision: Union[str, Sequence[str], None] = '2a69b275d501'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'request_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('server_name', sa.String(length=255), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('client_id', sa.String(length=255), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='ok', nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'request_events_server_ts_index', 'request_events', ['server_name', 'ts'], unique=False
    )
    op.create_index('request_events_ts_index', 'request_events', ['ts'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('request_events_ts_index', table_name='request_events')
    op.drop_index('request_events_server_ts_index', table_name='request_events')
    op.drop_table('request_events')
