# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言约定

代码注释、日志、commit message 均使用中文。Commit 遵循 conventional commits 格式：`feat(scope):` / `fix(scope):` / `tweak(scope):` + 中文描述。

## 常用命令

```bash
# 启动 Bot（需先启动 PostgreSQL 并完成迁移，配置 .env）
python -m src.main

# 运行全部测试（需要可连接的 PostgreSQL，测试直接操作真实数据库并 TRUNCATE 表）
pytest

# 运行单个测试文件 / 单个测试
pytest tests/test_coin_service_pg.py
pytest tests/test_coin_service_pg.py::test_name

# 数据库迁移
alembic upgrade head                                  # 应用迁移
alembic revision --autogenerate -m "描述"              # 创建迁移
alembic downgrade -1                                  # 回滚一步

# Docker 部署
docker compose up -d
docker compose logs -f bot_app
docker compose exec bot_app alembic upgrade head
docker compose --profile ollama up -d                 # 本地向量模式

# Web 前端开发（Vue 3 + Vite）
cd src/guidance && npm run dev    # 新人引导 UI（后端 uvicorn src.guidance.app:app，端口 8001）
cd src/lobby && npm run dev       # 大厅 UI（端口 8002）
```

本地开发时数据库连接：本机运行连 `localhost:5432`，容器内连 `db:5432`（由 `RUNNING_IN_DOCKER` 环境变量区分，见 `src/database/database.py`）。数据库必须使用 ParadeDB 镜像（`paradedb/paradedb:latest-pg16`），依赖 pgvector 和 BM25。

## 架构

### 启动流程（src/main.py）

`asyncio.run(main())` → `load_dotenv()` → 初始化数据库（`chat_db_manager` + `world_book_db_manager`）→ 从目录加载 AI 工具并注入全局 `ai_service` 单例 → 启动 `DiscordBot(commands.Bot)`。

Cog 加载在 `setup_hook()` 中动态完成：扫描 `src/chat/cogs/` **和** 所有 `src/chat/features/*/cogs/` 目录下的 `.py` 文件（跳过 `__` 开头和 `image_generation_cog.py`）。新增 feature 只要把 cog 放进 `features/<name>/cogs/` 即被自动发现。

### 分层结构

- `src/chat/features/<feature>/` — 功能模块（好感度、经济、游戏、塔罗、世界书等），每个模块内含 `cogs/`（Discord 命令/事件）和 `service/`（业务逻辑）
- `src/chat/services/` — 跨 feature 的服务层（消息处理、prompt、审核、embedding 等）
- `src/chat/services/ai/service.py` — 全局单例 `ai_service`：多 Provider 调度、故障转移、工具调用
- `src/database/` — SQLAlchemy 异步 PostgreSQL 层（`models.py` 定义全部 ORM 模型）

### 双数据库（重要）

- **PostgreSQL (ParadeDB)**：主数据库，SQLAlchemy async 引擎在 `src/database/database.py`，模型在 `src/database/models.py`，schema 按 `economy.` / `user.` 等 PostgreSQL schema 划分。向量检索用 pgvector(HNSW) + ParadeDB BM25。
- **SQLite (`data/chat.db`)**：遗留数据库，由 `src/chat/utils/database.py` 的 `chat_db_manager`（同步 sqlite3 包装）管理，存聊天设置、频道禁言等。
- `world_book_db_manager`（`src/chat/features/world_book/database/`）独立初始化。

新功能优先写入 PostgreSQL（async SQLAlchemy），除非明确属于遗留 SQLite 域。

### AI Provider 体系

- Provider 实现在 `src/chat/services/ai/providers/`，继承 `BaseProvider` 并实现 `generate`（现有 gemini / openai / deepseek）。
- **Provider 和模型配置不读 .env**，存于 PostgreSQL，运行时通过 Discord 内 `/聊天设置` 命令管理，`ai_service.initialize()` 启动时从库中加载。
- 向量模式由 `VECTOR_MODE` 控制（`none` / `api` / `local`），embedding 由 `embedding_factory.py` 按模式选择。

### AI 工具（函数调用）机制

工具放在 `src/chat/features/tools/functions/` 下的单个 `.py` 文件中：

- `tool_loader.load_tools_from_directory` 自动导入模块，收集其中的**公共异步函数**作为工具，并从函数签名的 Pydantic 模型自动生成 JSON Schema —— 不需要手动写 schema，也不存在 `@register_tool` 装饰器（README 中的说法已过时）。
- 可选：用 `@tool_metadata(name=..., description=..., emoji=..., category=...)` 为管理面板提供展示元数据。
- 通过 `.env` 的 `DISABLED_TOOLS`（逗号分隔模块名）禁用工具。

### 身份通用化（config/bot.yaml）

Bot 名称、货币名、社区名等全部身份信息集中在 `config/bot.yaml`，由 `src/config.py` 加载为全局常量（`config.BOT_NAME`、`config.CURRENCY_NAME` 等），提示词在模块加载时替换占位信息。**不要在代码或提示词中硬编码 Bot 名称/货币名/社区名**，一律引用 `src/config.py` 的常量。背景见 `GENERALIZATION_PLAN.md`。

### Web 服务

四个独立 FastAPI 应用（均为 `uvicorn` 启动，Docker 内由 Caddy 反代）：

| 服务 | 启动 | 端口 |
|------|------|------|
| blackjack_web | `uvicorn src.chat.features.games.blackjack-web.app:app` | 8000 |
| guidance_web | `uvicorn src.guidance.app:app` | 8001 |
| lobby_web | `uvicorn src.lobby.app:app` | 8002 |
| diary | `uvicorn src.diary.app:app` | 8003 |

`src/guidance/` 与 `src/lobby/` 目录同时包含 FastAPI 后端（`app.py`）和 Vue 3 前端（`src/` 子目录），通过 Discord Embedded App SDK 集成。

### 测试约定

测试**不 mock 数据库**：`tests/conftest.py` 直连真实 PostgreSQL（读 `DATABASE_URL` 或 `POSTGRES_*`/`DB_HOST` 环境变量），每个 fixture 前后 TRUNCATE 相关表。服务依赖（如 db_manager）才用 mock。

### 权限体系

`.env` 中 `DEVELOPER_USER_IDS` / `ADMIN_ROLE_IDS`（逗号分隔 Discord ID）控制管理权限；`GUILD_ID` 设置后命令仅同步到开发服务器（即时生效），未设置则全局同步（最长 1 小时生效）。
