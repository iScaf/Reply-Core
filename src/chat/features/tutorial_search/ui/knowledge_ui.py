# -*- coding: utf-8 -*-
"""教程知识库管理的 Discord UI：视图、按钮与模态框（自包含，从原商店 UI 迁出）。"""

from __future__ import annotations

import discord
import logging
from typing import List, Dict, Any
from datetime import datetime, timedelta
from enum import Enum, auto

from src.chat.features.tutorial_search.services.tutorial_manage_service import (
    tutorial_manage_service,
)
from src.chat.features.tutorial_search.services.thread_settings_service import (
    thread_settings_service,
)

log = logging.getLogger(__name__)


class TutorialModal(discord.ui.Modal, title="添加新的知识库教程"):
    """添加教程的模态框"""

    def __init__(self, view: "KnowledgeView"):
        super().__init__(timeout=300)
        self.view = view
        self.title_input = discord.ui.TextInput(
            label="教程标题",
            placeholder="请输入一个简洁明了的标题",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
        )
        self.description_input = discord.ui.TextInput(
            label="教程描述/内容",
            placeholder="请详细输入教程的内容...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        thread_id = self.view.thread_id
        if not thread_id:
            await interaction.followup.send(
                "❌ 错误：无法找到当前帖子的ID。请确保你在一个帖子中。", ephemeral=True
            )
            return

        success = await tutorial_manage_service.add_tutorial(
            title=self.title_input.value,
            description=self.description_input.value,
            author_id=interaction.user.id,
            author_name=interaction.user.display_name,
            thread_id=thread_id,
        )

        if success:
            await interaction.followup.send("✅ 你的教程已成功提交！", ephemeral=True)
            # 刷新视图以显示新教程
            await self.view.initialize(force_refresh=True)
            embed = await self.view.create_embed()
            if self.view.interaction:
                await self.view.interaction.edit_original_response(
                    embeds=[embed], view=self.view
                )
        else:
            await interaction.followup.send(
                "❌ 提交教程时发生错误，请稍后再试或联系管理员。", ephemeral=True
            )


class EditTutorialModal(discord.ui.Modal, title="编辑知识库教程"):
    """编辑教程的模态框（预填充数据）"""

    def __init__(
        self,
        view: "KnowledgeView",
        tutorial_id: int,
        current_data: Dict[str, Any],
    ):
        super().__init__(timeout=300)
        self.view = view
        self.tutorial_id = tutorial_id
        self.title_input = discord.ui.TextInput(
            label="教程标题",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
            default=current_data.get("title", ""),
        )
        self.description_input = discord.ui.TextInput(
            label="教程描述/内容",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
            default=current_data.get("description", ""),
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        success = await tutorial_manage_service.update_tutorial(
            tutorial_id=self.tutorial_id,
            title=self.title_input.value,
            description=self.description_input.value,
            author_id=interaction.user.id,
        )

        if success:
            await interaction.followup.send("✅ 你的教程已成功更新！", ephemeral=True)
            await self.view.initialize(force_refresh=True)
            self.view.enter_listing_mode()
            embed = await self.view.create_embed()
            if self.view.interaction:
                await self.view.interaction.edit_original_response(
                    embeds=[embed], view=self.view
                )
        else:
            await interaction.followup.send(
                "❌ 更新教程时发生错误，请稍后再试或联系管理员。", ephemeral=True
            )


class ConfirmationModal(discord.ui.Modal, title="确认删除"):
    """一个用于确认操作的简单模态框。"""

    def __init__(self, on_confirm_callback):
        super().__init__(timeout=180)
        self._on_confirm = on_confirm_callback
        self.add_item(
            discord.ui.TextInput(
                label="输入 '确认删除' 以继续",
                placeholder="确认删除",
                style=discord.TextStyle.short,
                required=True,
                max_length=4,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        text_input = self.children[0]
        if isinstance(text_input, discord.ui.TextInput) and text_input.value.strip() == "确认删除":
            await self._on_confirm(interaction)
        else:
            await interaction.response.send_message(
                "输入不匹配，操作已取消。", ephemeral=True
            )


class SearchModeButton(discord.ui.Button):
    """切换当前帖子搜索模式的按钮。"""

    def __init__(self):
        super().__init__(
            label="切换搜索模式", style=discord.ButtonStyle.primary, emoji="🔍"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        view = self.view
        assert isinstance(view, KnowledgeView)

        thread_id = view.thread_id
        if not thread_id:
            await interaction.followup.send(
                "❌ 错误：无法找到当前帖子的ID。请确保你在一个帖子中。", ephemeral=True
            )
            return

        current_mode = await thread_settings_service.get_search_mode(str(thread_id))
        new_mode = "PRIORITY" if current_mode == "ISOLATED" else "ISOLATED"
        await thread_settings_service.set_search_mode(str(thread_id), new_mode)

        await view.initialize()
        embed = await view.create_embed()
        await interaction.edit_original_response(embeds=[embed], view=view)


class AddTutorialButton(discord.ui.Button):
    """添加新教程的按钮。"""

    def __init__(self):
        super().__init__(
            label="添加新教程", style=discord.ButtonStyle.success, emoji="➕"
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        assert isinstance(view, KnowledgeView)
        modal = TutorialModal(view)
        await interaction.response.send_modal(modal)


class ManageTutorialsButton(discord.ui.Button):
    """管理现有教程的按钮。"""

    def __init__(self):
        super().__init__(
            label="管理现有教程", style=discord.ButtonStyle.secondary, emoji="📝"
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        assert isinstance(view, KnowledgeView)

        if not view.tutorials:
            await interaction.response.send_message(
                "你还没有可以管理的教程。", ephemeral=True
            )
            return

        view.enter_management_mode()
        embed = await view.create_embed()
        await interaction.response.edit_message(embeds=[embed], view=view)


class TutorialActionSelect(discord.ui.Select):
    """一个用于选择要执行操作（编辑/删除）的教程的选择菜单。"""

    def __init__(self, tutorials: List[Dict[str, Any]]):
        options = [
            discord.SelectOption(
                label=tutorial["title"][:100],
                value=str(tutorial["id"]),
                description=f"ID: {tutorial['id']}",
                emoji="📝",
            )
            for tutorial in tutorials
        ]
        options = options[:25]
        super().__init__(
            placeholder="选择一个你要操作的教程...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        assert isinstance(view, KnowledgeView)
        view.selected_tutorial_id = int(self.values[0])
        view.update_components()
        await interaction.response.edit_message(view=view)


class EditTutorialButton(discord.ui.Button):
    """编辑所选教程的按钮。"""

    def __init__(self):
        super().__init__(
            label="编辑教程",
            style=discord.ButtonStyle.primary,
            emoji="✏️",
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        assert isinstance(view, KnowledgeView)
        if not view.selected_tutorial_id:
            await interaction.response.send_message(
                "请先从下拉菜单中选择一个要编辑的教程。", ephemeral=True
            )
            return

        tutorial_data = await tutorial_manage_service.get_tutorial_by_id(
            view.selected_tutorial_id
        )
        if not tutorial_data:
            await interaction.response.send_message(
                "❌ 无法找到所选教程的数据，它可能已被删除。", ephemeral=True
            )
            return

        modal = EditTutorialModal(
            view=view,
            tutorial_id=view.selected_tutorial_id,
            current_data=tutorial_data,
        )
        await interaction.response.send_modal(modal)


class DeleteTutorialButton(discord.ui.Button):
    """删除所选教程的按钮。"""

    def __init__(self):
        super().__init__(
            label="删除教程",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        assert isinstance(view, KnowledgeView)
        if not view.selected_tutorial_id:
            await interaction.response.send_message(
                "请先选择一个教程。", ephemeral=True
            )
            return

        async def confirm_delete_callback(modal_interaction: discord.Interaction):
            await modal_interaction.response.defer(ephemeral=True)

            tutorial_id_to_delete = view.selected_tutorial_id
            if not tutorial_id_to_delete:
                await modal_interaction.followup.send(
                    "❌ 发生错误：没有选中的教程。", ephemeral=True
                )
                return

            success = await tutorial_manage_service.delete_tutorial(
                tutorial_id=tutorial_id_to_delete, author_id=interaction.user.id
            )

            if success:
                await modal_interaction.followup.send(
                    "✅ 教程已成功删除。", ephemeral=True
                )
                await view.initialize(force_refresh=True)
                view.enter_listing_mode()
                embed = await view.create_embed()
                if view.interaction:
                    await view.interaction.edit_original_response(
                        embeds=[embed], view=view
                    )
            else:
                await modal_interaction.followup.send(
                    "❌ 删除失败。你可能不是该教程的作者，或者教程已被删除。",
                    ephemeral=True,
                )

        modal = ConfirmationModal(on_confirm_callback=confirm_delete_callback)
        await interaction.response.send_modal(modal)


class BackToListButton(discord.ui.Button):
    """返回教程列表的按钮。"""

    def __init__(self):
        super().__init__(label="返回列表", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        assert isinstance(view, KnowledgeView)
        view.enter_listing_mode()
        embed = await view.create_embed()
        await interaction.response.edit_message(embeds=[embed], view=view)


class RefreshButton(discord.ui.Button):
    """刷新教程列表的按钮。"""

    def __init__(self):
        super().__init__(label="刷新", style=discord.ButtonStyle.secondary, emoji="🔄")

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        assert isinstance(view, KnowledgeView)
        await view.initialize(force_refresh=True)
        embed = await view.create_embed()
        await interaction.response.edit_message(embeds=[embed], view=view)


class PanelState(Enum):
    LISTING = auto()
    MANAGING = auto()


class KnowledgeView(discord.ui.View):
    """教程知识库管理视图（UGC 入口，从原商店迁移为独立界面）"""

    def __init__(
        self,
        author: discord.User | discord.Member,
        thread_id: int | None,
    ):
        super().__init__(timeout=180)
        self.author = author
        self.thread_id = thread_id
        self.interaction: discord.Interaction | None = None
        self.tutorials: List[Dict[str, Any]] = []
        self.selected_tutorial_id: int | None = None
        self._state = PanelState.LISTING
        self.update_components()

    async def initialize(self, force_refresh: bool = False):
        """异步初始化：拉取当前用户的教程列表。"""
        if force_refresh or not self.tutorials:
            self.tutorials = await tutorial_manage_service.get_tutorials_by_author(
                self.author.id
            )
        self.update_components()

    def enter_management_mode(self):
        self._state = PanelState.MANAGING

    def enter_listing_mode(self):
        self._state = PanelState.LISTING
        self.selected_tutorial_id = None

    def update_components(self):
        """根据当前状态重建组件。"""
        self.clear_items()
        if self._state == PanelState.MANAGING:
            edit_button = EditTutorialButton()
            delete_button = DeleteTutorialButton()
            if self.selected_tutorial_id:
                edit_button.disabled = False
                delete_button.disabled = False
            components: List[discord.ui.Item] = [
                TutorialActionSelect(self.tutorials),
                edit_button,
                delete_button,
                BackToListButton(),
            ]
        else:
            components = [
                SearchModeButton(),
                AddTutorialButton(),
                ManageTutorialsButton(),
                RefreshButton(),
            ]
        for component in components:
            self.add_item(component)

    async def create_embed(self) -> discord.Embed:
        if self._state == PanelState.MANAGING:
            return discord.Embed(
                title="管理现有教程",
                description="请从下方的下拉菜单中选择一个教程，然后选择你要执行的操作（编辑或删除）。",
                color=discord.Color.dark_orange(),
            )

        # 列表视图
        search_mode = "ISOLATED"
        if self.thread_id:
            search_mode = await thread_settings_service.get_search_mode(
                str(self.thread_id)
            )

        mode_name = "隔离模式" if search_mode == "ISOLATED" else "优先模式"
        mode_desc = (
            "只检索当前帖子的教程和基础库，当前帖子教程优先。"
            if search_mode == "ISOLATED"
            else "检索所有教程，但优先显示当前帖子的教程。"
        )

        embed = discord.Embed(
            title="知识库管理",
            description="在这里管理你提交的教程。",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name=f"🔍 当前搜索模式：{mode_name}",
            value=mode_desc,
            inline=False,
        )

        if not self.tutorials:
            embed.add_field(
                name="你的教程", value="你还没有提交任何教程。", inline=False
            )
        else:
            for tutorial in self.tutorials[:25]:
                created_at_utc = tutorial.get("created_at")
                if created_at_utc and isinstance(created_at_utc, datetime):
                    created_at_str = (created_at_utc + timedelta(hours=8)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                else:
                    created_at_str = "日期未知"
                embed.add_field(
                    name=f"📝 {tutorial['title']}",
                    value=f"创建于: {created_at_str}",
                    inline=False,
                )
        return embed

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        if self.interaction:
            try:
                await self.interaction.edit_original_response(view=self)
            except (discord.NotFound, discord.errors.InteractionResponded):
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "这不是你的知识库管理界面哦！", ephemeral=True
            )
            return False
        return True
