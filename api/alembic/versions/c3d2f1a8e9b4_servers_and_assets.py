"""servers + server_assets

Move authoritative per-server state (spec, uploaded assets, git/template build
files) off the per-node `server-data` Swarm volume into Postgres so platform-api
can run multiple replicas without node-local state. Specs are imported from the
volume on first boot by app.services.spec_import (idempotent).

Revision ID: c3d2f1a8e9b4
Revises: b7e1c0a4d2f8
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d2f1a8e9b4'
down_revision: Union[str, Sequence[str], None] = 'b7e1c0a4d2f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'servers',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('spec', sa.JSON(), nullable=False),
        sa.Column('mode', sa.String(length=32), server_default='structured', nullable=False),
        sa.Column('build_files', sa.LargeBinary(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )
    op.create_table(
        'server_assets',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('server_name', sa.String(length=255), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('server_name', 'filename', name='server_assets_name_uq'),
    )
    op.create_index('server_assets_server_index', 'server_assets', ['server_name'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('server_assets_server_index', table_name='server_assets')
    op.drop_table('server_assets')
    op.drop_table('servers')
