"""新增 bot.web_chat_messages：Web 问答演示聊天记录持久化

支持 Web 管理台问答演示的历史记录回看（分页向上加载）。
每行一条 user/assistant 消息，assistant 行附带工具轨迹与 token 用量。

Revision ID: c5d8e12f7a60
Revises: b7e4a90c2f15
Create Date: 2026-09-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d8e12f7a60'
down_revision: Union[str, Sequence[str], None] = 'b7e4a90c2f15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'web_chat_messages',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False,
                  comment='user / assistant'),
        sa.Column('content', sa.Text(), nullable=False, comment='消息正文'),
        sa.Column('tool_trace', sa.JSON(), nullable=True,
                  comment='assistant 消息关联的工具调用轨迹'),
        sa.Column('model', sa.String(length=200), nullable=True,
                  comment='生成使用的模型'),
        sa.Column('elapsed_ms', sa.Integer(), nullable=True, comment='总耗时（毫秒）'),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True,
                  comment='输入 token 数（来自 API usage）'),
        sa.Column('completion_tokens', sa.Integer(), nullable=True,
                  comment='输出 token 数（来自 API usage）'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        schema='bot',
    )
    op.create_index(
        'ix_web_chat_created_at',
        'web_chat_messages',
        ['created_at'],
        unique=False,
        schema='bot',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_web_chat_created_at', table_name='web_chat_messages', schema='bot')
    op.drop_table('web_chat_messages', schema='bot')
