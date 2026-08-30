import logging
from typing import Optional, List, Dict, Any
import json
import psycopg2

import asyncio

# 导入新的服务依赖
from src.chat.services.ai.service import ai_service
from src.chat.config import chat_config
from src.chat.features.community_settings.services.incremental_rag_service import (
    incremental_rag_service,
)

log = logging.getLogger(__name__)


class CommunitySettingsService:
    """
    社区设定知识库：使用向量数据库进行语义搜索，以查找相关的设定条目。
    同时负责用户档案（个人记忆载体）的查询与自动建档。
    """

    def __init__(self, ai_svc):
        self.ai_service = ai_svc
        log.info("CommunitySettingsService (ParadeDB Hybrid Search version) 初始化完成。")

    def is_ready(self) -> bool:
        """检查服务是否已准备好（所有依赖项都可用）。"""
        # 本地向量模式不需要 ai_service
        from src.chat.services.embedding_factory import is_vector_enabled

        if chat_config.VECTOR_MODE == "local":
            return is_vector_enabled()
        # API 向量模式需要 ai_service
        return self.ai_service.is_available()

    async def find_entries(
        self,
        latest_query: str,
        user_id: int,
        guild_id: int,
        user_name: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        n_results: int = chat_config.RAG_N_RESULTS_DEFAULT,
        max_distance: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        根据用户的最新问题和可选的对话历史，总结查询并查找相关的社区设定条目。

        Args:
            latest_query: 用户最新的原始消息。
            user_id: 用户的 Discord ID。
            guild_id: 服务器的 Discord ID。
            conversation_history: (可选) 用于生成查询的特定对话历史。
            n_results: 要返回的结果数量。
            max_distance: RAG 搜索的距离阈值，用于过滤不相关的结果。

        Returns:
            一个包含最相关条目信息的字典列表。
        """
        if not self.is_ready() or not latest_query:
            if not latest_query:
                log.debug("latest_query 为空，跳过 RAG 搜索。")
            else:
                log.info("RAG功能未启用：未配置API密钥，跳过检索。")
            return []

        # 在将历史记录传递给RAG总结器之前，移除最后一条由系统注入的上下文提示
        history_for_rag = conversation_history.copy() if conversation_history else []
        if history_for_rag and history_for_rag[-1].get("role") == "model":
            if (
                "我会按好感度和上下文综合回复"
                in history_for_rag[-1].get("parts", [""])[0]
            ):
                history_for_rag.pop()
                log.debug("已为RAG总结移除系统注入的上下文提示。")

        # RAG 查询重写功能已移除，直接使用清理后的原始查询
        from src.chat.services.regex_service import regex_service
        import re

        clean_query = regex_service.clean_user_input(latest_query)
        # 进一步移除 Discord 提及（包括 <@123456789> 和 @username 格式）
        summarized_query = re.sub(r"<@!?&?\d+>\s*", "", clean_query)
        summarized_query = re.sub(r"@\S+\s*", "", summarized_query).strip()
        log.info(f"原始查询: '{summarized_query}'")

        # 确保查询字符串不为空
        if not summarized_query.strip():
            log.warning(f"最终查询为空，无法进行RAG搜索 (user_id: {user_id})")
            return []

        # 执行混合搜索
        try:
            from src.chat.features.community_settings.services.knowledge_search_service import (
                knowledge_search_service,
            )

            search_results = await knowledge_search_service.search(summarized_query)

            if search_results:
                search_brief = [
                    f"{r['id']}(score:{1 - r['distance']:.4f})" for r in search_results
                ]
                log.debug(f"社区设定混合搜索简报 (ID 和 Score): {search_brief}")
            else:
                log.debug("社区设定混合搜索未返回任何结果。")

            return search_results
        except Exception as e:
            log.error(f"在社区设定混合搜索过程中发生错误: {e}", exc_info=True)
            return []

    def add_setting_entry(
        self,
        title: str,
        name: str,
        content_text: str,
        category_name: str,
        contributor_id: Optional[int] = None,
    ) -> bool:
        """
        向 community_settings.documents 表添加一个新的设定条目。

        Args:
            title: 设定条目的标题
            name: 设定条目的名称
            content_text: 设定条目的内容文本
            category_name: 设定条目的类别名称
            contributor_id: 贡献者的 Discord ID (可选)

        Returns:
            bool: 添加成功返回 True，否则返回 False
        """
        log.info(
            f"尝试向 ParadeDB 添加社区设定条目: title='{title}', name='{name}', category='{category_name}'"
        )

        # 直接使用 RAG 服务的数据库连接
        conn = incremental_rag_service._get_parade_connection()
        if not conn:
            return False

        cursor = None
        try:
            from psycopg2.extras import DictCursor

            cursor = conn.cursor(cursor_factory=DictCursor)

            # 准备内容数据
            content_dict = {"description": content_text}
            content_json_str = json.dumps(content_dict, ensure_ascii=False)

            # 准备 source_metadata
            import time
            import re

            clean_title = re.sub(r"[^\w\u4e00-\u9fff]", "_", title)[:50]
            external_id = f"{clean_title}_{int(time.time())}"

            source_metadata = {
                "id": external_id,
                "title": title,
                "name": name,
                "content_json": content_json_str,
                "category": category_name,
                "contributor_id": contributor_id,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "approved",
            }
            source_metadata_str = json.dumps(source_metadata, ensure_ascii=False)

            # 插入新条目并获取返回的 id
            cursor.execute(
                """
                INSERT INTO community_settings.documents (external_id, title, full_text, source_metadata, created_at, updated_at)
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (external_id, title, content_json_str, source_metadata_str),
            )

            new_entry = cursor.fetchone()
            if not new_entry or "id" not in new_entry:
                raise Exception("未能获取新插入条目的 ID。")

            new_id = new_entry["id"]
            conn.commit()
            log.info(f"成功添加社区设定条目: ID={new_id} ({title})")

            log.info(f"正在为新设定条目 ID={new_id} 创建异步向量化任务...")
            asyncio.create_task(
                incremental_rag_service.process_setting_entry(str(new_id))
            )

            return True

        except Exception as e:
            log.error(f"添加社区设定条目到 ParadeDB 时发生错误: {e}", exc_info=True)
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()

    def _query_profile(self, discord_id: int) -> Optional[Dict[str, Any]]:
        """通过 Discord ID 查询 community.member_profiles 中的用户档案（个人记忆载体）。"""
        conn = incremental_rag_service._get_parade_connection()
        if not conn:
            log.error("ParadeDB 连接不可用，无法获取用户档案。")
            return None

        try:
            from psycopg2.extras import RealDictCursor

            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        discord_id,
                        title,
                        personal_summary,
                        source_metadata
                    FROM community.member_profiles
                    WHERE discord_id = %s
                    """,
                    (str(discord_id),),
                )
                profile = cursor.fetchone()

            if profile:
                return dict(profile)
            return None
        except psycopg2.Error as e:
            log.error(f"从 ParadeDB 查询用户档案时发生数据库错误: {e}", exc_info=True)
            return None
        except Exception as e:
            log.error(f"查询用户档案时发生未知错误: {e}", exc_info=True)
            return None

    async def get_profile_by_discord_id(
        self, discord_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        通过 Discord ID 获取用户档案（不自动创建）。
        """
        profile = self._query_profile(discord_id)
        if profile:
            log.debug(f"成功找到 discord_id {discord_id} 的用户档案。")
        else:
            log.debug(f"未找到 discord_id {discord_id} 的用户档案。")
        return profile

    async def get_or_create_profile(
        self, discord_id: int, display_name: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        获取用户档案；不存在时自动创建最小档案（个人记忆功能的载体）。

        名片提交审核流已移除，个人记忆通过自动建档对所有用户生效。
        """
        profile = self._query_profile(discord_id)
        if profile:
            return profile

        # 自动建档
        conn = incremental_rag_service._get_parade_connection()
        if not conn:
            log.error("ParadeDB 连接不可用，无法自动创建用户档案。")
            return None

        import time

        external_id = f"auto_{discord_id}_{int(time.time())}"
        full_text = f"名称: {display_name or str(discord_id)}"
        source_metadata = {
            "name": display_name or str(discord_id),
            "source": "auto_created",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        try:
            from psycopg2.extras import RealDictCursor

            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO community.member_profiles
                    (external_id, discord_id, title, full_text, source_metadata, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (discord_id) DO NOTHING
                    RETURNING discord_id, title, personal_summary, source_metadata
                    """,
                    (
                        external_id,
                        str(discord_id),
                        display_name or str(discord_id),
                        full_text,
                        json.dumps(source_metadata, ensure_ascii=False),
                    ),
                )
                row = cursor.fetchone()
            conn.commit()
            if row:
                log.info(f"已为用户 {discord_id} 自动创建档案（个人记忆载体）。")
                return dict(row)
            # 并发冲突（已存在），重新查询
            return self._query_profile(discord_id)
        except psycopg2.Error as e:
            log.error(f"自动创建用户档案失败: {e}", exc_info=True)
            conn.rollback()
            return None


# 全局单例
community_settings_service = CommunitySettingsService(ai_service)
