"""relax bge_embedding NOT NULL on tutorials.knowledge_chunks

Revision ID: relax_bge_null
Revises: add_content_filter_keywords
Create Date: 2026-08-22

将 tutorials.knowledge_chunks.bge_embedding 的 NOT NULL 约束放宽为可空。
原因：VECTOR_MODE=local 且 embedding_model=qwen 时向量写入 qwen_embedding 列，
bge_embedding 留空，原 NOT NULL 约束会导致插入失败。
HNSW 索引会自动跳过 NULL 行，向量检索不受影响。

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "relax_bge_null"
down_revision: Union[str, Sequence[str], None] = "add_content_filter_keywords"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        text(
            "ALTER TABLE tutorials.knowledge_chunks "
            "ALTER COLUMN bge_embedding DROP NOT NULL"
        )
    )


def downgrade() -> None:
    # 注意：回滚前需确保表中不存在 bge_embedding 为 NULL 的行，否则会失败
    op.execute(
        text(
            "ALTER TABLE tutorials.knowledge_chunks "
            "ALTER COLUMN bge_embedding SET NOT NULL"
        )
    )
