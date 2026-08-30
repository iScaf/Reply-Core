# -*- coding: utf-8 -*-
"""社区设定的 Discord 命令入口：/社区设定 提交。"""

import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.chat.features.community_settings.ui.contribution_modal import (
    CommunitySettingModal,
    AVAILABLE_CATEGORIES,
)

log = logging.getLogger(__name__)


class CommunitySettingCommandsCog(commands.Cog):
    """社区设定管理命令"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="设定提交", description="向社区设定知识库提交一条新设定（进入公开投票审核）")
    async def submit_setting(self, interaction: discord.Interaction):
        modal = CommunitySettingModal()
        await interaction.response.send_modal(modal)

    @app_commands.command(name="设定说明", description="查看社区设定知识库的类别说明")
    async def setting_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="社区设定知识库说明",
            description=(
                "社区设定是本社区共享的知识库，AI 在对话中会自主检索它来回答问题。\n"
                "提交的设定将进入公开投票审核，通过后自动向量化入库。"
            ),
            color=discord.Color.blurple(),
        )
        categories = "\n".join(f"- **{c}**" for c in AVAILABLE_CATEGORIES)
        embed.add_field(name="可用类别", value=categories, inline=False)
        embed.add_field(
            name="提交方式",
            value="使用 `/设定提交` 命令，填写类别、标题和内容。",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CommunitySettingCommandsCog(bot))
    log.info("社区设定命令已加载。")
