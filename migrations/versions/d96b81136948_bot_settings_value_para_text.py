"""bot_settings value para text

Revision ID: d96b81136948
Revises: d2b9d3a630b3
Create Date: 2026-10-01 18:45:06.372246

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd96b81136948'
down_revision: Union[str, Sequence[str], None] = 'd2b9d3a630b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # o JSON de job_runs (/health) estourava o limite de 200 chars
    op.alter_column("bot_settings", "value", existing_type=sa.String(length=200), type_=sa.Text())


def downgrade() -> None:
    op.alter_column("bot_settings", "value", existing_type=sa.Text(), type_=sa.String(length=200))
