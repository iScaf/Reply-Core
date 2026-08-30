# -*- coding: utf-8 -*-
"""Reply-Core 数据库初始化脚本。

读取项目根目录 .env，创建业务 schema、全部表（含 HNSW 向量索引）、
ParadeDB BM25 索引，并用 `alembic stamp head` 标记迁移版本。

适用于全新数据库的一次性初始化；后续 schema 变更走 alembic 增量迁移：
    alembic revision --autogenerate -m "描述"
    alembic upgrade head

用法（项目根目录）：
    python scripts/init_db.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"), override=True)

import psycopg2  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

BUSINESS_SCHEMAS = (
    "tutorials",
    "community_settings",
    "community",
    "forum",
    "conversation",
    "bot",
    "ai_config",
    "content_filter",
    "user",
)

BM25_INDEXES = (
    "CREATE INDEX idx_chunk_text_bm25 ON tutorials.knowledge_chunks "
    "USING bm25 (id, ((chunk_text)::pdb.chinese_compatible)) WITH (key_field=id)",
    "CREATE INDEX idx_cs_chunks_bm25 ON community_settings.chunks "
    "USING bm25 (id, ((chunk_text)::pdb.chinese_compatible)) WITH (key_field=id)",
    "CREATE INDEX idx_forum_content_bm25 ON forum.forum_threads "
    "USING bm25 (id, ((content)::pdb.chinese_compatible), ((thread_name)::pdb.chinese_compatible)) "
    "WITH (key_field=id)",
    "CREATE INDEX idx_conv_text_bm25 ON conversation.conversation_blocks "
    "USING bm25 (id, ((conversation_text)::pdb.chinese_compatible)) WITH (key_field=id)",
)


def main() -> None:
    db = os.getenv("POSTGRES_DB", "bot_db")
    user = os.getenv("POSTGRES_USER", "user")
    password = os.getenv("POSTGRES_PASSWORD", "password")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")

    # 1. 建库（不存在时）
    conn = psycopg2.connect(dbname="postgres", user=user, password=password, host=host, port=port)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{db}"')
            print(f"[init_db] 数据库 {db} 已创建")
        else:
            print(f"[init_db] 数据库 {db} 已存在")
    conn.close()

    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    engine = create_engine(url)

    # 2. 扩展 + schema
    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pg_search"))
        for schema in BUSINESS_SCHEMAS:
            c.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    print("[init_db] 扩展与 schema 就绪")

    # 3. 建表（含 HNSW 向量索引）
    from src.database.models import Base

    Base.metadata.create_all(engine)
    print("[init_db] 数据表创建完成（含 HNSW 索引）")

    # 4. ParadeDB BM25 索引（语法无法用 SQLAlchemy 表达，手工执行；先删保证幂等）
    with engine.begin() as c:
        for ddl in BM25_INDEXES:
            idx_name = ddl.split("ON ")[0].replace("CREATE INDEX", "").strip()
            schema = ddl.split(" ON ")[1].split(".")[0]
            c.execute(text(f"DROP INDEX IF EXISTS {schema}.{idx_name}"))
            c.execute(text(ddl))
    print("[init_db] BM25 索引创建完成")

    # 5. 标记 alembic 版本
    os.system(f'python -m alembic stamp head')

    # 6. 验证
    with engine.connect() as c:
        n = c.execute(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ANY(:schemas)"
        ), {"schemas": list(BUSINESS_SCHEMAS)}).scalar()
        bm25 = c.execute(text("SELECT count(*) FROM pg_indexes WHERE indexname LIKE '%bm25%'")).scalar()
        hnsw = c.execute(text("SELECT count(*) FROM pg_indexes WHERE indexname LIKE '%hnsw%'")).scalar()
    print(f"[init_db] 完成：业务表 {n} 张，BM25 索引 {bm25} 个，HNSW 索引 {hnsw} 个")


if __name__ == "__main__":
    main()
