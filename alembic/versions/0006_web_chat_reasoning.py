"""web_chat_messages 增加 reasoning 列：持久化思维链，历史展示与现场一致

Revision ID: f7a2c34d9b82
Revises: e6f9a23b8c71
Create Date: 2026-09-02 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a2c34d9b82'
down_revision: Union[str, Sequence[str], None] = 'e6f9a23b8c71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'web_chat_messages',
        sa.Column('reasoning', sa.Text(), nullable=True,
                  comment='思维链全文（多轮以分隔符拼接；历史展示用）'),
        schema='bot',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('web_chat_messages', 'reasoning', schema='bot')
