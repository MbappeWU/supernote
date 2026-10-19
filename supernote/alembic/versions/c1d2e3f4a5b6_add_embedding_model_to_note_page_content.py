"""add embedding_model to note page content

Revision ID: c1d2e3f4a5b6
Revises: b7397248a96b
Create Date: 2026-10-19 02:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from supernote.server.config import ServerConfig

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b7397248a96b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_configured_embedding_model() -> str:
    context_config = op.get_context().config
    server_config: ServerConfig | None = (
        context_config.attributes.get("server_config")
        if context_config is not None
        else None
    )
    if server_config is None:
        server_config = ServerConfig.load()
    if server_config.ai.embedding is not None:
        return server_config.ai.embedding.model
    return server_config.gemini_embedding_model


def upgrade() -> None:
    with op.batch_alter_table("f_note_page_content") as batch_op:
        batch_op.add_column(sa.Column("embedding_model", sa.String(), nullable=True))

    embedding_model = _get_configured_embedding_model()
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE f_note_page_content "
            "SET embedding_model = :model "
            "WHERE embedding IS NOT NULL AND embedding_model IS NULL"
        ),
        {"model": embedding_model},
    )
    bind.execute(
        sa.text(
            "UPDATE f_system_task "
            "SET data = :model "
            "WHERE task_type = 'EMBEDDING_GENERATION' "
            "AND status = 'COMPLETED' AND data IS NULL"
        ),
        {"model": embedding_model},
    )


def downgrade() -> None:
    with op.batch_alter_table("f_note_page_content") as batch_op:
        batch_op.drop_column("embedding_model")
