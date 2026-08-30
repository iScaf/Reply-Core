# -*- coding: utf-8 -*-

import os
import logging
import sqlite3
import psycopg2
from psycopg2.extras import DictCursor
from typing import Optional, Union

log = logging.getLogger(__name__)


def get_parade_db_connection() -> Optional[psycopg2.extensions.connection]:
    """
    为管理面板创建一个同步的 psycopg2 数据库连接到 Parade DB。
    """
    try:
        if os.getenv("RUNNING_IN_DOCKER"):
            db_host = os.getenv("DB_HOST", "db")
        else:
            db_host = os.getenv("DB_HOST", "localhost")

        conn = psycopg2.connect(
            dbname=os.getenv("POSTGRES_DB", "bot_db"),
            user=os.getenv("POSTGRES_USER", "user"),
            password=os.getenv("POSTGRES_PASSWORD", "password"),
            host=db_host,
            port=os.getenv("DB_PORT", "5432"),
        )
        # conn.cursor_factory is deprecated, use conn.cursor(cursor_factory=...)
        return conn
    except psycopg2.Error as e:
        log.error(f"无法连接到 Parade DB 数据库: {e}")
        return None


def get_db_connection(
    db_type: str, db_path: Optional[str] = None
) -> Optional[Union[sqlite3.Connection, psycopg2.extensions.connection]]:
    """
    根据类型获取相应的数据库连接。
    """
    if db_type == "parade":
        return get_parade_db_connection()
    else:
        log.error(f"无效的数据库类型: db_type={db_type}")
        return None


def get_cursor(connection: Union[sqlite3.Connection, psycopg2.extensions.connection]):
    """
    根据连接类型获取合适的 cursor。
    对于 psycopg2，我们使用 DictCursor。
    """
    if isinstance(connection, psycopg2.extensions.connection):
        return connection.cursor(cursor_factory=DictCursor)
    return connection.cursor()
