import discord
import logging

from src import config
from src.chat.services.submission_service import submission_service
from src.chat.features.community_settings.services.community_settings_service import (
    community_settings_service,
)

log = logging.getLogger(__name__)

# 定义可用的类别列表
AVAILABLE_CATEGORIES = ["社区信息", "社区文化", "社区大事件", "俚语", "通用知识"]


class CommunitySettingModal(discord.ui.Modal, title="贡献社区设定"):
    """用于用户提交社区设定条目的模态窗口"""

    def __init__(self):
        super().__init__()

        self.category_input = discord.ui.TextInput(
            label="类别",
            placeholder=f"请输入类别，例如：{', '.join(AVAILABLE_CATEGORIES)}",
            max_length=50,
            required=True,
        )
        self.add_item(self.category_input)

        self.title_input = discord.ui.TextInput(
            label="标题",
            placeholder="请输入设定条目的标题",
            max_length=100,
            required=True,
        )
        self.add_item(self.title_input)

        self.content_input = discord.ui.TextInput(
            label="内容",
            placeholder="请输入详细内容",
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
        )
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        """当用户提交模态窗口时调用"""
        # 延迟响应以处理后续操作
        await interaction.response.defer(ephemeral=True)

        # 收集和验证输入
        category = self.category_input.value.strip()
        title = self.title_input.value.strip()
        content = self.content_input.value.strip()

        if category not in AVAILABLE_CATEGORIES:
            await interaction.followup.send(
                f"无效的类别。请从以下选项中选择: {', '.join(AVAILABLE_CATEGORIES)}",
                ephemeral=True,
            )
            return

        if not all([category, title, content]):
            await interaction.followup.send(
                "类别、标题和内容均不能为空。", ephemeral=True
            )
            return

        # 管理员直接入库，无需审核
        if interaction.user.id in config.DEVELOPER_USER_IDS:
            await self.developer_direct_add(interaction, category, title, content)
            return

        # 构造数据并调用 SubmissionService（进入公开投票审核流）
        setting_data = {
            "category_name": category,
            "title": title,
            "name": title,
            "content_text": content,
            "contributor_id": interaction.user.id,
            "contributor_name": interaction.user.display_name,
        }

        pending_id = await submission_service.submit_community_setting(
            interaction, setting_data
        )

        if pending_id:
            await interaction.followup.send(
                f"✅ 您的社区设定 **{title}** 已成功提交审核！\n请关注频道内的公开投票。",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "提交审核时发生错误，请稍后再试。", ephemeral=True
            )

    async def developer_direct_add(
        self,
        interaction: discord.Interaction,
        category_name: str,
        title: str,
        content_text: str,
    ):
        """开发者直接添加设定条目，无需审核（内部会触发增量向量化）"""
        success = community_settings_service.add_setting_entry(
            title=title,
            name=title,
            content_text=content_text,
            category_name=category_name,
            contributor_id=interaction.user.id,
        )

        if success:
            await interaction.followup.send(
                f"✅ **管理员直通**: 社区设定 **{title}** 已成功添加并触发向量化，无需审核。",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "❌ 添加时发生内部错误，请检查日志。", ephemeral=True
            )
