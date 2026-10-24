"""unify schedule task_id and task_list_id as string columns

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-10-19 05:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
}


def upgrade() -> None:
    op.execute(
        """
        UPDATE t_schedule_task
        SET client_task_list_id = (
            SELECT g.client_task_list_id
            FROM t_schedule_task_group g
            WHERE g.user_id = t_schedule_task.user_id
              AND g.task_list_id = t_schedule_task.task_list_id
              AND g.client_task_list_id IS NOT NULL
        )
        WHERE client_task_list_id IS NULL
        """
    )

    with op.batch_alter_table("t_schedule_task_group") as batch_op:
        batch_op.alter_column(
            "task_list_id",
            existing_type=sa.BigInteger(),
            type_=sa.String(),
            existing_nullable=False,
        )

    with op.batch_alter_table("t_schedule_task") as batch_op:
        batch_op.alter_column(
            "task_id",
            existing_type=sa.BigInteger(),
            type_=sa.String(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "task_list_id",
            existing_type=sa.BigInteger(),
            type_=sa.String(),
            existing_nullable=False,
        )

    op.execute(
        """
        UPDATE t_schedule_task_group
        SET task_list_id = client_task_list_id
        WHERE client_task_list_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE t_schedule_task
        SET task_id = COALESCE(client_task_id, task_id),
            task_list_id = COALESCE(client_task_list_id, task_list_id)
        WHERE client_task_id IS NOT NULL OR client_task_list_id IS NOT NULL
        """
    )

    with op.batch_alter_table(
        "t_schedule_task", naming_convention=_NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_index("ix_t_schedule_task_client_task_list_id")
        batch_op.drop_index("ix_t_schedule_task_client_task_id")
        batch_op.drop_column("client_task_list_id")
        batch_op.drop_column("client_task_id")
        batch_op.drop_constraint("pk_t_schedule_task", type_="primary")
        batch_op.create_primary_key("pk_t_schedule_task", ["task_id", "user_id"])

    with op.batch_alter_table(
        "t_schedule_task_group", naming_convention=_NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_index("ix_t_schedule_task_group_client_task_list_id")
        batch_op.drop_column("client_task_list_id")
        batch_op.drop_constraint("pk_t_schedule_task_group", type_="primary")
        batch_op.create_primary_key(
            "pk_t_schedule_task_group", ["task_list_id", "user_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table(
        "t_schedule_task_group", naming_convention=_NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(
            sa.Column("client_task_list_id", sa.String(), nullable=True)
        )
        batch_op.create_index(
            "ix_t_schedule_task_group_client_task_list_id", ["client_task_list_id"]
        )
        batch_op.alter_column(
            "task_list_id",
            existing_type=sa.String(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )
        batch_op.drop_constraint("pk_t_schedule_task_group", type_="primary")
        batch_op.create_primary_key("pk_t_schedule_task_group", ["task_list_id"])

    with op.batch_alter_table(
        "t_schedule_task", naming_convention=_NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column("client_task_id", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("client_task_list_id", sa.String(), nullable=True)
        )
        batch_op.create_index("ix_t_schedule_task_client_task_id", ["client_task_id"])
        batch_op.create_index(
            "ix_t_schedule_task_client_task_list_id", ["client_task_list_id"]
        )
        batch_op.alter_column(
            "task_id",
            existing_type=sa.String(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "task_list_id",
            existing_type=sa.String(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )
        batch_op.drop_constraint("pk_t_schedule_task", type_="primary")
        batch_op.create_primary_key("pk_t_schedule_task", ["task_id"])
