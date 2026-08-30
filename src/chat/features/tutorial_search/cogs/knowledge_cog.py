# -*- coding: utf-8 -*-
"""教程知识库的 Discord 命令入口：/知识库。"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.chat.features.tutorial_search.ui.knowledge_ui import KnowledgeView

log = logging.getLogger(__name__)


class KnowledgeCog(commands.Cog):
    """教程知识库管理命令"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="知识库", description="管理你提交的教程知识库（建议在帖子中使用）")
    async def knowledge_base(self, interaction: discord.Interaction):
        # 教程与帖子关联（thread_id），帖子内使用体验最佳
        thread_id = None
        if isinstance(interaction.channel, discord.Thread):
            thread_id = interaction.channel.id

        view = KnowledgeView(author=interaction.user, thread_id=thread_id)
        await view.initialize()
        embed = await view.create_embed()
        view.interaction = interaction
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(KnowledgeCog(bot))
    log.info("教程知识库命令已加载。")
