"""新增 ai_config.bot_persona：Bot 人设库（后台可编辑）

人设正文从 prompts.py 硬编码迁移到数据库（应用启动时 seed），
后台编辑保存后即时生效（缓存刷新），prompts.py 保留为兜底。

Revision ID: e6f9a23b8c71
Revises: c5d8e12f7a60
Create Date: 2026-09-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f9a23b8c71'
down_revision: Union[str, Sequence[str], None] = 'c5d8e12f7a60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'bot_persona',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False,
                  comment='人设唯一标识（default/gentle/自定义，对应 persona_style）'),
        sa.Column('display_name', sa.String(length=100), nullable=False, comment='后台展示名'),
        sa.Column('system_prompt', sa.Text(), nullable=False,
                  comment='完整人设正文（<character> 结构）'),
        sa.Column('is_default', sa.Integer(), nullable=False, server_default=sa.text('0'),
                  comment='1=默认人设（无用户偏好时使用）'),
        sa.Column('enabled', sa.Integer(), nullable=False, server_default=sa.text('1'), comment='1=启用'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        schema='ai_config',
    )
    op.create_index(
        'ix_bot_persona_name', 'bot_persona', ['name'], unique=True, schema='ai_config'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_bot_persona_name', table_name='bot_persona', schema='ai_config')
    op.drop_table('bot_persona', schema='ai_config')
