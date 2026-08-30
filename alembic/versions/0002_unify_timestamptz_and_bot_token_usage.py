"""统一时间列为 timestamptz，token_usage 挪入 bot schema

1. token_usage 从 public 搬移到 bot schema（归属统计域，与其余运行时表一致）。
2. token_usage.date 列由 timestamp 修正为 date（该列语义即按天统计，实际存的就是 date 对象）。
3. 业务 schema 下所有 timestamp without time zone 列统一为 timestamptz：
   现存 naive 值全部按 UTC 墙钟写入（服务器时区为 Etc/UTC），
   使用 AT TIME ZONE 'UTC' 明确解释，避免依赖会话时区的隐式转换。
   统一后 asyncpg 绑定要求 aware datetime，杜绝 naive/aware 混用类报错。

Revision ID: a3f7c2d91e48
Revises: 8b95b2f1710b
Create Date: 2026-08-30 21:55:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3f7c2d91e48'
down_revision: Union[str, Sequence[str], None] = '8b95b2f1710b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 本迁移转换的 naive timestamp 列显式清单（与 ORM 模型一一对应，downgrade 严格对称）
TIMESTAMP_COLUMNS: list[tuple[str, str, str]] = [
    # (schema, table, column)
    ("tutorials", "tutorial_documents", "created_at"),
    ("tutorials", "tutorial_documents", "updated_at"),
    ("tutorials", "knowledge_chunks", "created_at"),
    ("tutorials", "knowledge_chunks", "updated_at"),
    ("tutorials", "thread_settings", "created_at"),
    ("tutorials", "thread_settings", "updated_at"),
    ("community_settings", "documents", "created_at"),
    ("community_settings", "documents", "updated_at"),
    ("community_settings", "chunks", "created_at"),
    ("community_settings", "pending_entries", "created_at"),
    ("community_settings", "pending_entries", "expires_at"),
    ("community", "member_profiles", "created_at"),
    ("community", "member_profiles", "updated_at"),
    ("forum", "forum_threads", "created_at"),
    ("forum", "forum_threads", "created_at_db"),
    ("forum", "forum_threads", "updated_at"),
    ("conversation", "conversation_blocks", "start_time"),
    ("conversation", "conversation_blocks", "end_time"),
    ("conversation", "conversation_blocks", "created_at"),
    ("conversation", "conversation_blocks", "updated_at"),
    ("user", "user_tool_settings", "created_at"),
    ("user", "user_tool_settings", "updated_at"),
    ("user", "user_command_settings", "created_at"),
    ("user", "user_command_settings", "updated_at"),
    ("user", "user_persona_preference", "created_at"),
    ("user", "user_persona_preference", "updated_at"),
    ("user", "user_memory_notes", "created_at"),
    ("user", "user_memory_notes", "updated_at"),
    ("user", "user_warnings", "updated_at"),
    ("ai_config", "ai_providers", "created_at"),
    ("ai_config", "ai_providers", "updated_at"),
    ("ai_config", "ai_models", "created_at"),
    ("ai_config", "ai_models", "updated_at"),
    ("content_filter", "content_filter_keywords", "created_at"),
    ("bot", "global_settings", "updated_at"),
]


def _convert_columns(target_type: str, using_expr: str) -> None:
    """按显式清单批量转换列类型。

    using_expr 中 {col} 为带引号列名占位符，例如 '"col" AT TIME ZONE 'UTC''。
    """
    for schema, table, col in TIMESTAMP_COLUMNS:
        quoted = f'"{col}"'
        op.execute(
            f'ALTER TABLE "{schema}"."{table}" ALTER COLUMN {quoted} '
            f'TYPE {target_type} USING {using_expr.format(col=quoted)}'
        )


def upgrade() -> None:
    """Upgrade schema."""
    # 1. token_usage 搬入 bot schema（索引随表移动，无需单独处理）
    op.execute("ALTER TABLE token_usage SET SCHEMA bot")

    # 2. date 列 timestamp -> date（现存值为 UTC 午夜零点，按 UTC 转换）
    op.execute(
        'ALTER TABLE bot.token_usage ALTER COLUMN date TYPE date '
        "USING (date AT TIME ZONE 'UTC')::date"
    )

    # 3. naive timestamp -> timestamptz（按 UTC 解释现存值）
    _convert_columns("timestamptz", "{col} AT TIME ZONE 'UTC'")


def downgrade() -> None:
    """Downgrade schema."""
    # 反向：timestamptz -> naive timestamp（转回 UTC 墙钟）
    _convert_columns("timestamp", "{col} AT TIME ZONE 'UTC'")

    op.execute(
        'ALTER TABLE bot.token_usage ALTER COLUMN date TYPE timestamp '
        "USING date AT TIME ZONE 'UTC'"
    )
    op.execute("ALTER TABLE bot.token_usage SET SCHEMA public")
