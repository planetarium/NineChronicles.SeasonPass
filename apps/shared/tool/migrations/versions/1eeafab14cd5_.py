"""empty message

Revision ID: 1eeafab14cd5
Revises: 00a050a705c2, f0229efb5723
Create Date: 2023-11-10 11:44:28.736084

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1eeafab14cd5"
down_revision: Union[str, None] = ("00a050a705c2", "f0229efb5723")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
