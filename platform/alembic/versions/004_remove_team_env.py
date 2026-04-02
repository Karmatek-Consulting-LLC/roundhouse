"""Remove team-scoped MCP env and server.team_id

Revision ID: 004
Revises: 003
Create Date: 2026-04-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_team_env_vars_team_id", table_name="team_env_vars")
    op.drop_table("team_env_vars")
    op.drop_index("ix_server_owners_team_id", table_name="server_owners")
    op.drop_constraint("fk_server_owners_team_id_teams", "server_owners", type_="foreignkey")
    op.drop_column("server_owners", "team_id")


def downgrade() -> None:
    op.add_column(
        "server_owners",
        sa.Column("team_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_server_owners_team_id_teams",
        "server_owners",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_server_owners_team_id", "server_owners", ["team_id"])
    op.create_table(
        "team_env_vars",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "name", name="uq_team_env_vars_team_name"),
    )
    op.create_index("ix_team_env_vars_team_id", "team_env_vars", ["team_id"])
