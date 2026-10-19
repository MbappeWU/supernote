"""make schedule task_list_id nullable

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-10-19 06:35:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("t_schedule_task") as batch_op:
        batch_op.alter_column(
            "task_list_id",
            existing_type=sa.String(),
            nullable=True,
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE t_schedule_task SET task_list_id = NULL WHERE task_list_id IN ('', '0')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE t_schedule_task SET task_list_id = '0' WHERE task_list_id IS NULL"
        )
    )
    with op.batch_alter_table("t_schedule_task") as batch_op:
        batch_op.alter_column(
            "task_list_id",
            existing_type=sa.String(),
            nullable=False,
        )
