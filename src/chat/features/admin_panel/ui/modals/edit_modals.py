# -*- coding: utf-8 -*-

import discord
import logging
import sqlite3
import json

from src.chat.features.community_settings.services.incremental_rag_service import (
    incremental_rag_service,
)
from src.chat.features.personal_memory.services.personal_memory_service import (
    personal_memory_service,
)

from ..typing import AnyDBView

log = logging.getLogger(__name__)


# --- 新增：编辑个人记忆的模态窗口 ---
class EditMemoryModal(discord.ui.Modal):
    def __init__(
        self, db_view: AnyDBView, user_id: int, member_name: str, current_summary: str
    ):
        # --- 标题截断 ---
        title_prefix = "编辑 "
        title_suffix = " 的记忆"
        # 计算 `member_name` 的最大允许长度
        max_name_len = 45 - len(title_prefix) - len(title_suffix)

        truncated_name = member_name
        # 如果 `member_name` 太长，则截断并添加省略号
        if len(member_name) > max_name_len:
            # 减去3是为了给 "..." 留出空间
            truncated_name = member_name[: max_name_len - 3] + "..."

        super().__init__(title=f"{title_prefix}{truncated_name}{title_suffix}")
        self.db_view = db_view
        self.user_id = user_id

        # --- 截断摘要以符合 Discord 4000 字符限制 ---
        max_summary_length = 4000
        truncated_summary = current_summary
        if current_summary and len(current_summary) > max_summary_length:
            truncated_summary = (
                current_summary[: max_summary_length - 50]
                + "\n\n[⚠️ 记忆摘要过长，已截断。完整内容请在数据库中查看或使用命令行工具。]"
            )
            log.warning(
                f"用户 {user_id} 的记忆摘要过长 ({len(current_summary)} 字符)，已截断至 {max_summary_length} 字符。"
            )

        self.summary_input = discord.ui.TextInput(
            label="个人记忆摘要",
            style=discord.TextStyle.paragraph,
            default=truncated_summary,
            max_length=max_summary_length,  # Discord TextInput 最大长度
            required=False,
        )
        self.add_item(self.summary_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new_summary = self.summary_input.value.strip()

        try:
            await personal_memory_service.update_summary_manually(
                self.user_id, new_summary
            )
            log.info(
                f"管理员 {interaction.user.display_name} 更新了用户 {self.user_id} 的记忆摘要。"
            )
            await interaction.followup.send(
                f"✅ 用户 `{self.user_id}` 的记忆摘要已成功更新。", ephemeral=True
            )
        except Exception as e:
            log.error(f"更新用户 {self.user_id} 的记忆时出错: {e}", exc_info=True)
            await interaction.followup.send(f"更新记忆时发生错误: {e}", ephemeral=True)


# --- 编辑条目的模态窗口 ---
class EditModal(discord.ui.Modal):
    def __init__(
        self,
        db_view: AnyDBView,
        table_name: str,
        item_id: str,
        current_data: sqlite3.Row,
    ):
        # 构造并截断标题以防止超长
        self.db_view = db_view  # 修正: 将传入的 db_view 实例赋值给 self
        raw_title = self.db_view._get_entry_title(dict(current_data))
        truncated_title = (raw_title[:30] + "...") if len(raw_title) > 30 else raw_title
        modal_title = f"编辑: {truncated_title} (#{item_id})"
        if len(modal_title) > 45:
            modal_title = modal_title[:42] + "..."

        super().__init__(title=modal_title)
        self.db_view = db_view
        self.table_name = table_name
        self.item_id = item_id
        self.current_data = current_data

        # 获取除 'id' 外的所有列
        columns = [col for col in self.current_data.keys() if col.lower() != "id"]

        # Discord 模态窗口最多支持5个组件
        if len(columns) > 4:
            # 这里的 self.title 赋值也会影响最终标题，所以也要截断
            base_title = f"编辑: {truncated_title} (#{item_id})"
            suffix = " (前4字段)"
            if len(base_title) + len(suffix) > 45:
                allowed_len = 45 - len(suffix) - 3  # 3 for "..."
                base_title = base_title[:allowed_len] + "..."
            self.title = base_title + suffix
            columns_to_display = columns[:4]
        else:
            columns_to_display = columns

        # 动态添加文本输入框
        for col in columns_to_display:
            value = self.current_data[col]
            style = discord.TextStyle.short
            default_text = str(value) if value is not None else ""

            # 检查值是否为字典或可能是 JSON 字符串
            is_json_like = isinstance(value, dict) or (
                isinstance(value, str) and value.strip().startswith(("{", "["))
            )

            if is_json_like:
                try:
                    # 如果是字符串，先加载它；如果是字典，直接使用
                    json_data = json.loads(value) if isinstance(value, str) else value
                    # 转储为格式化的 JSON 字符串用于显示
                    default_text = json.dumps(json_data, indent=2, ensure_ascii=False)
                    style = discord.TextStyle.paragraph
                except (json.JSONDecodeError, TypeError):
                    # 如果解析或转储失败，则回退到简单的字符串转换
                    default_text = str(value) if value is not None else ""

            # 如果内容很长，即使不是 JSON，也使用段落样式
            if style == discord.TextStyle.short and len(default_text) > 100:
                style = discord.TextStyle.paragraph

            self.add_item(
                discord.ui.TextInput(
                    label=col,
                    default=default_text,
                    style=style,
                    required=False,  # 允许字段为空
                )
            )

    async def on_submit(self, interaction: discord.Interaction):
        conn = self.db_view._get_db_connection()
        if not conn:
            await interaction.response.send_message("数据库连接失败。", ephemeral=True)
            return

        try:
            from src.chat.features.admin_panel.services import db_services

            cursor = db_services.get_cursor(conn)

            update_fields = []
            update_values = []

            # 从模态窗口的子组件中获取更新后的值
            for component in self.children:
                if isinstance(component, discord.ui.TextInput):
                    # 使用 %s 作为占位符
                    update_fields.append(f'"{component.label}" = %s')
                    update_values.append(component.value)

            update_values.append(self.item_id)

            # 构建并执行 SQL 更新语句
            # 使用 %s 作为 WHERE 子句的占位符
            sql = (
                f"UPDATE {self.table_name} SET {', '.join(update_fields)} WHERE id = %s"
            )

            log.debug(f"Executing SQL: {sql}")
            log.debug(f"With params: {tuple(update_values)}")

            cursor.execute(sql, tuple(update_values))
            conn.commit()
            log.info(
                f"管理员 {interaction.user.display_name} 成功更新了表 '{self.table_name}' 中 ID 为 {self.item_id} 的记录。"
            )

            await interaction.response.send_message(
                f"✅ 记录 `#{self.item_id}` 已成功更新。", ephemeral=True
            )

            # --- RAG 更新 (通用) ---
            log.info(
                f"开始为更新后的条目 {self.item_id} (表: {self.table_name}) 同步向量数据库..."
            )
            await incremental_rag_service.delete_entry(self.item_id)

            # 根据表名选择合适的处理函数
            if self.table_name == "community_settings.documents":
                await incremental_rag_service.process_setting_entry(self.item_id)
            # 'pending_entries' 通常不直接进入 RAG，所以这里不处理

            log.info(f"条目 {self.item_id} 的向量数据库同步完成。")

            # 刷新原始的数据库浏览器视图
            await self.db_view.update_view()

        except sqlite3.Error as e:
            log.info(
                f"管理员 {interaction.user.display_name} 成功更新了表 '{self.table_name}' 中 ID 为 {self.item_id} 的记录。"
            )
            log.error(f"更新数据库记录失败: {e}", exc_info=True)
            await interaction.response.send_message(f"更新失败: {e}", ephemeral=True)
        finally:
            conn.close()
