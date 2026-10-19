"""add update_time to schedule task group

Revision ID: e7f8a9b0c1d2
Revises: d4e5f6a7b8c9
Create Date: 2026-10-19 06:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("t_schedule_task_group") as batch_op:
        batch_op.add_column(
            sa.Column(
                "update_time", sa.BigInteger(), nullable=False, server_default="0"
            )
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE t_schedule_task_group SET update_time = create_time WHERE update_time = 0"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("t_schedule_task_group") as batch_op:
        batch_op.drop_column("update_time")
