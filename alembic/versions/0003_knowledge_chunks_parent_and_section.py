"""knowledge_chunks 增加 parent_id 与 section_path 列

支持上传文档的分块检索（small-to-big）：
- parent_id：子块指向节级父块；父块本身 parent_id 为 NULL 且不建向量，
  仅在检索命中子块后回取节级上下文时使用。
- section_path：面包屑章节路径（源自 Markdown 标题层级），
  孤立列存储，避免污染参与 BM25 检索的 chunk_text。

Revision ID: b7e4a90c2f15
Revises: a3f7c2d91e48
Create Date: 2026-08-31 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e4a90c2f15'
down_revision: Union[str, Sequence[str], None] = 'a3f7c2d91e48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'knowledge_chunks',
        sa.Column('parent_id', sa.Integer(), nullable=True,
                  comment='父块ID（节级父块）；NULL=父块或独立单块。父块不建向量，仅用于检索后回取节级上下文（small-to-big）。'),
        schema='tutorials',
    )
    op.create_foreign_key(
        'fk_knowledge_chunks_parent_id',
        'knowledge_chunks',
        'knowledge_chunks',
        ['parent_id'],
        ['id'],
        source_schema='tutorials',
        referent_schema='tutorials',
    )
    op.add_column(
        'knowledge_chunks',
        sa.Column('section_path', sa.Text(), nullable=True,
                  comment="面包屑章节路径，如 '第二章 部署 > 2.1 环境准备'（源自上传文档的 Markdown 标题层级）"),
        schema='tutorials',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('knowledge_chunks', 'section_path', schema='tutorials')
    op.drop_constraint(
        'fk_knowledge_chunks_parent_id',
        'knowledge_chunks',
        type_='foreignkey',
        schema='tutorials',
    )
    op.drop_column('knowledge_chunks', 'parent_id', schema='tutorials')
