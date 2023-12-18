"""empty message

Revision ID: c3c5e9c8ff0f
Revises: 508b09040fa7, 825471e94b6f
Create Date: 2023-12-18 23:04:59.697263

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3c5e9c8ff0f'
down_revision: Union[str, None] = ('508b09040fa7', '825471e94b6f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
