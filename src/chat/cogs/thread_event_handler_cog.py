# -*- coding: utf-8 -*-

import logging
import discord
from discord.ext import commands
import asyncio

from src.chat.config import chat_config

log = logging.getLogger(__name__)


class ThreadEventHandlerCog(commands.Cog):
    """
    帖子（Thread）事件中央处理器。
    监听新帖创建事件，延迟后分发给 ForumSyncCog 进行 RAG 索引。
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _dispatch_to_forum_sync(self, thread: discord.Thread):
        """检查条件并延迟后，分发给 ForumSyncCog 进行RAG索引。"""
        if thread.parent_id not in chat_config.FORUM_SEARCH_CHANNEL_IDS:
            return

        delay = chat_config.FORUM_SYNC_DELAY_SECONDS
        log.info(
            f"[ForumSync Dispatch] 帖子 {thread.id} 符合RAG索引条件。等待 {delay} 秒后开始处理..."
        )
        await asyncio.sleep(delay)
        forum_sync_cog = self.bot.get_cog("ForumSyncCog")
        if forum_sync_cog:
            assert isinstance(forum_sync_cog, commands.Cog)
            if hasattr(forum_sync_cog, "handle_new_thread"):
                await getattr(forum_sync_cog, "handle_new_thread")(thread)
            else:
                log.warning(
                    "[ForumSync Dispatch] ForumSyncCog 没有handle_new_thread方法。"
                )
        else:
            log.warning("[ForumSync Dispatch] 找不到 ForumSyncCog 实例，任务取消。")

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """监听新帖子的创建事件，启动延迟 RAG 索引任务。"""
        log.info(
            f"[Central Dispatcher] 检测到新帖子 '{thread.name}' ({thread.id})，开始分发任务..."
        )
        asyncio.create_task(self._dispatch_to_forum_sync(thread))


async def setup(bot: commands.Bot):
    """将此Cog添加到机器人中。"""
    await bot.add_cog(ThreadEventHandlerCog(bot))
    log.info("中央帖子事件处理器 (ThreadEventHandlerCog) 已加载。")
