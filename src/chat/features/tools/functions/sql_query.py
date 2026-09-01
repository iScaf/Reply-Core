# -*- coding: utf-8 -*-
"""
SQL 只读查询工具 - 供 AI 检索数据库内所有表的数据与结构

安全设计（仅查询，不允许任何写操作）：
1. 只读事务：asyncpg transaction(readonly=True)，即使 SQL 意外包含写语句也会被 PG 拒绝
2. 语句白名单：仅允许 SELECT / WITH / SHOW / EXPLAIN / TABLE 开头的单条语句
3. 关键字黑名单：INSERT/UPDATE/DELETE/DDL 等一律拒绝（防御纵深）
4. 单语句限制：拒绝分号拼接的多语句
5. 结果限流：无 LIMIT 的 SELECT 自动包一层 LIMIT 50，防止大结果集拖垮响应
"""

import json
import logging
import re
from typing import Any, Dict, List

import asyncpg
from pydantic import BaseModel, Field

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

MAX_ROWS = 50

# 语句白名单前缀（大写比较）
_ALLOWED_PREFIXES = ("SELECT", "WITH", "SHOW", "EXPLAIN", "TABLE")
# 危险关键字黑名单（词边界匹配，防御白名单绕过，如 CTE 内带写语句）
_FORBIDDEN_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"VACUUM|REINDEX|CALL|DO|SET|RESET|COPY|LISTEN|NOTIFY|REPLACE)\b",
    re.IGNORECASE,
)


class SqlQueryParams(BaseModel):
    """SQL 只读查询参数"""

    sql: str = Field(
        ...,
        description=(
            "要执行的 PostgreSQL 只读查询语句。仅支持 SELECT / WITH / SHOW / "
            "EXPLAIN，必须单条语句。查表结构可用 information_schema.columns / "
            "pg_catalog.pg_tables。查询大量数据时请自行加 LIMIT。"
        ),
    )


@tool_metadata(
    name="数据库查询",
    description="以只读方式查询数据库：查看所有表的结构与数据（仅 SELECT，禁止修改/删除）",
    emoji="🗄️",
    category="查询",
)
async def sql_query(
    params: SqlQueryParams,
    **kwargs,
) -> Dict[str, Any]:
    """
    在只读事务中执行 SQL 查询，返回列名与数据行。

    可查询数据库内所有 schema 的表（教程库 / 社区设定 / 论坛 / 记忆 /
    运行时配置等），也可查 information_schema 了解表结构。
    """
    sql = (params.sql or "").strip().rstrip(";")
    if not sql:
        return {"error": "SQL 语句为空"}

    # 多语句防御：白名单语句中除 EXPLAIN 外不应再含分号
    if ";" in sql:
        return {"error": "仅允许单条语句，请去掉多余的分号"}

    first_word = sql.split(None, 1)[0].upper() if sql.split() else ""
    if first_word not in _ALLOWED_PREFIXES:
        return {
            "error": f"仅允许 SELECT / WITH / SHOW / EXPLAIN / TABLE 查询，收到: {first_word}"
        }
    if _FORBIDDEN_PATTERN.search(sql):
        return {"error": "检测到被禁止的写操作关键字，本工具仅支持只读查询"}

    # 无 LIMIT 的 SELECT 自动包一层，防止全表大结果
    effective_sql = sql
    if first_word in ("SELECT", "TABLE") and "LIMIT" not in sql.upper():
        effective_sql = f"SELECT * FROM ({sql}) AS _limited LIMIT {MAX_ROWS}"
        note = f"（原查询无 LIMIT，已自动限制 {MAX_ROWS} 行）"
    else:
        note = None

    from src.chat.utils.database import get_database_url

    dsn = get_database_url(sync=False).replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        async with conn.transaction(readonly=True):
            stmt = await conn.prepare(effective_sql)
            columns = [attr.name for attr in stmt.get_attributes()]
            records = await stmt.fetch()
    except asyncpg.PostgresError as e:
        msg = str(e).strip()
        log.warning(f"[SQL工具] 查询执行失败: {msg}")
        # 列/表不存在时附带结构发现建议，引导模型先查 information_schema
        hint = ""
        if "does not exist" in msg:
            hint = (
                "提示：表或列名可能不准确。可先执行 "
                "SELECT table_schema, table_name, column_name FROM "
                "information_schema.columns WHERE table_name = '表名' "
                "确认实际结构后再查询。"
            )
        return {"error": f"SQL 执行失败: {msg}", **({"hint": hint} if hint else {})}
    finally:
        await conn.close()

    rows = [
        {
            col: (
                value.isoformat()
                if hasattr(value, "isoformat")
                else value if isinstance(value, (int, float, bool, str)) else str(value)
            )
            for col, value in zip(columns, record.values())
        }
        for record in records
    ]

    result: Dict[str, Any] = {
        "columns": columns,
        "row_count": len(rows),
        "rows": rows,
    }
    if note:
        result["note"] = note
    if rows:
        result["preview"] = json.dumps(rows[:3], ensure_ascii=False, default=str)[:600]
    log.info(f"[SQL工具] 查询完成，返回 {len(rows)} 行")
    return result
