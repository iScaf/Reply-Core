# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言约定

代码注释、日志、commit message 均使用中文。Commit 遵循 conventional commits 格式：`feat(scope):` / `fix(scope):` / `tweak(scope):` + 中文描述。

## 项目定位

Reply-Core：Discord AI 知识库助手（技术分享演示项目）。三套 RAG 知识库（教程/社区设定/论坛帖）+ Agentic 检索问答 + 个人记忆。全部数据在 PostgreSQL（ParadeDB 镜像），**不使用 SQLite**。

## 常用命令

```bash
# 启动 Bot（需先完成数据库初始化，配置 .env）
python -m src.main

# 一键初始化数据库（建 schema/表/HNSW/BM25 索引，并 alembic stamp head）
python scripts/init_db.py

# 数据库增量迁移
alembic revision --autogenerate -m "描述"
alembic upgrade head

# 运行全部测试（需要可连接的 PostgreSQL，测试直接操作真实数据库并 TRUNCATE 表）
pytest

# 单个测试
pytest tests/test_community_settings_service_pg.py
```

本地开发数据库连接：本机连 `localhost:5432`，容器内连 `db:5432`（由 `RUNNING_IN_DOCKER` 区分，见 `src/chat/utils/database.py` 的 `get_database_url` 与 `src/database/database.py`）。数据库必须使用 ParadeDB 镜像（`paradedb/paradedb:latest-pg16`），依赖 pgvector 和 pg_search（BM25）。

## 架构

### 启动流程（src/main.py）

`asyncio.run(main())` → `load_dotenv()` → `chat_db_manager.init_async()`（表结构由 Alembic 管理，此处仅日志）→ 加载 AI 工具 → `DiscordBot`。

Cog 加载在 `setup_hook()` 中动态完成：扫描 `src/chat/cogs/` **和** 所有 `src/chat/features/*/cogs/` 目录下的 `.py` 文件（跳过 `__` 开头）。新增 feature 只要把 cog 放进 `features/<name>/cogs/` 即被自动发现。

### 分层结构

- `src/chat/features/<feature>/` — 功能模块，内含 `cogs/`（Discord 命令/事件）和 `service/`（业务逻辑）
- `src/chat/services/` — 跨 feature 服务层（prompt 构建、审核、个人记忆服务）
- `src/chat/services/ai/service.py` — 全局单例 `ai_service`：多 Provider 调度、故障转移、工具调用
- `src/database/` — SQLAlchemy 异步 PostgreSQL 层（`models.py` 按 schema 划分全部 ORM 模型）

### PostgreSQL Schema 划分（唯一数据库）

| Schema | 内容 |
|---|---|
| `tutorials` | 教程文档、向量块、帖子搜索模式 |
| `community_settings` | 社区设定条目（documents/chunks）、待审核队列（pending_entries） |
| `community` | `member_profiles`（个人记忆载体，首次对话自动建档） |
| `forum` | 论坛帖索引、同步进度（processed_threads/backfill_status） |
| `conversation` | 对话记忆块 |
| `bot` | Bot 运行时数据：全局设置/黑名单/聊天配置/冷却/禁言/统计（原遗留 SQLite 已迁入） |
| `user` | 用户工具/命令/人设偏好设置、记忆笔记、警告记录 |
| `ai_config` | AI Provider 与模型配置（加密存 API Key） |
| `content_filter` | 内容过滤关键词 |

### AI Provider 体系

- Provider 实现在 `src/chat/services/ai/providers/`，继承 `BaseProvider` 并实现 `generate`。
- **Provider 和模型配置不读 .env**，存于 PG `ai_config` schema，运行时通过 `/聊天设置` 管理，`ai_service.initialize()` 启动时加载。
- 向量模式由 `VECTOR_MODE` 控制（`none` / `api` / `local`），embedding 由 `embedding_factory.py` 按模式选择；本地模式用 Ollama 双模型（BGE/qwen），当前用哪列由 `bot.global_settings` 的 `embedding_model` 键决定。

### AI 工具（函数调用）机制

工具放在 `src/chat/features/tools/functions/` 下的单个 `.py` 文件中：

- `tool_loader.load_tools_from_directory` 自动导入模块，收集其中的**公共异步函数**作为工具，并从函数签名的 Pydantic 模型自动生成 JSON Schema——不需要手动写 schema。
- 可选：用 `@tool_metadata(name=..., description=..., emoji=..., category=...)` 为管理面板提供展示元数据。
- 核心工具：`search`（五 scope 混合检索）、`gather_context`、`manage_memory`、`web_search`、`summarize_channel`、`issue_user_warning`、`tarot_reading`。

### 混合检索（关键 SQL）

- `community_settings/services/knowledge_search_service.py`：pgvector `<=>`（HNSW）+ ParadeDB `@@@` BM25（`paradedb.score`）+ RRF 融合，单条 SQL（CTE: semantic_search / keyword_search / fused_ranks）。
- BM25/HNSW 索引语法无法用 SQLAlchemy 表达：BM25 由 `scripts/init_db.py` 与初始迁移 `op.execute` 手工创建（`pdb.chinese_compatible` 分词）。

### 身份配置（config/bot.yaml）

Bot 名称、社区名等身份信息集中在 `config/bot.yaml`（当前身份：小回 / Reply-Core 技术社区），由 `src/config.py` 加载为全局常量。`prompts.py` 的 `_apply_identity()` 在 bot.yaml 身份与默认身份不一致时做字面量替换。**不要在代码或提示词中硬编码 Bot 名称/社区名**，一律引用 `src/config.py` 的常量。

### 测试约定

测试**不 mock 数据库**：`tests/conftest.py` 直连真实 PostgreSQL（读 `DATABASE_URL` 或 `POSTGRES_*`/`DB_HOST` 环境变量），每个 fixture 前后 TRUNCATE 相关表。

### 权限体系

`.env` 中 `DEVELOPER_USER_IDS` / `ADMIN_ROLE_IDS`（逗号分隔 Discord ID）控制管理权限；`GUILD_ID` 设置后命令仅同步到开发服务器（即时生效），未设置则全局同步（最长 1 小时生效）。

### 二期计划

Web 管理页面（知识库管理 + 问答演示）尚未实现；当前全部管理入口为 Discord 斜杠命令与 `/admin` 面板。
