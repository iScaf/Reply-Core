# Reply-Core

一个基于 Discord 的 AI 知识库助手：**三套 RAG 知识库（教程 / 社区设定 / 论坛帖）+ Agentic 检索问答 + 个人记忆**，全部数据存于 PostgreSQL（ParadeDB），零 SQLite。

> 本项目从社区 Bot 项目裁剪而来，聚焦「AI 如何查资料」的完整 RAG 链路，适合作为技术分享的演示项目。

## 核心功能

### 三套 RAG 知识库

| 知识库 | PG Schema | 内容来源 | 检索方式 |
|---|---|---|---|
| 教程库（UGC） | `tutorials` | 用户通过 `/知识库` 提交，帖主可增删改 | 混合检索 + 帖子亲和（ISOLATED/PRIORITY 双模式） |
| 社区设定 | `community_settings` | 用户通过 `/设定提交` 贡献，公开投票审核后入库 | 混合检索（向量 + BM25 + RRF） |
| 论坛帖索引 | `forum` | 自动同步指定论坛频道的新帖（实时 + 每日历史回溯） | 混合检索 + 元数据过滤 |

### Agentic 检索

知识**不预注入** prompt——AI 在工具循环里自主决定是否调用 `search` 工具（scope：`tutorial` / `community_settings` / `forum` / `channel` / `memory`）。工具 Schema 由 Pydantic 模型自动生成，零手写 JSON Schema。

### 混合检索（一条 SQL 的三路融合）

```
语义路：pgvector 余弦距离（HNSW 索引加速） → RANK()
关键词路：ParadeDB BM25（pdb.chinese_compatible 中文分词） → RANK()
融合：RRF = 1/(60+rank语义) + 1/(60+rank关键词)
```

### 个人记忆

用户首次与 Bot 对话时自动建档（`community.member_profiles`）。对话满 N 条自动总结为记忆块（`conversation.conversation_blocks`，向量检索实现"永久记忆"），AI 也可通过 `manage_memory` 工具写结构化记忆笔记。

### 多模型调度与容错

Provider 抽象（gemini / openai_compatible / deepseek），配置存 PG 不存 .env，Discord 内 `/聊天设置` 热切换。三层容错：同 Provider 重试 → 跨 Provider 故障转移 → 降级纯对话。支持两阶段管线（Stage 1 极简提示跑工具路由，Stage 2 完整人设写作）。

### 其他

- **塔罗占卜**：本地 78 张牌 + Pillow 拼图，零外部依赖
- **联网搜索**：SearXNG（可选部署）
- **管理面板**：`/admin` 查看编辑知识条目、向量库元数据、对话记忆
- **内容安全**：关键词过滤 + 用户警告/黑名单 + 频道禁言投票

## 技术栈

- Python 3.11+ / discord.py / SQLAlchemy (async) / Alembic
- PostgreSQL：**ParadeDB 镜像**（`paradedb/paradedb:latest-pg16` = PG16 + pgvector + pg_search BM25 三合一）
- Embedding：Ollama 本地双模型（BGE-M3 + Qwen3-Embedding，双向量列 halfvec(1024)）或 Gemini API
- AI Provider：google-genai / openai 兼容接口

## 快速开始（本地开发）

```bash
# 0. 准备：PostgreSQL 使用 ParadeDB 镜像（依赖 pgvector 和 pg_search）
#    本地开发建议直接 docker run 一个 ParadeDB，或用下方 docker compose

# 1. 安装依赖
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env    # 填入 DISCORD_TOKEN、数据库连接等

# 3. 初始化数据库（建 schema、建表、BM25/HNSW 索引，并 stamp 迁移版本）
python scripts/init_db.py

# 4. （本地向量模式）安装 Ollama 并拉取 embedding 模型
ollama pull qwen3-embedding:0.6b

# 5. 启动 Bot
python -m src.main
```

## Docker 部署

```bash
docker compose up -d                          # 启动 bot + db + searxng
docker compose --profile ollama up -d         # 附带本地向量服务
docker compose exec bot_app python scripts/init_db.py   # 首次初始化数据库
docker compose logs -f bot_app
```

## 常用命令

| 命令 | 说明 |
|---|---|
| `/知识库` | 帖主管理自己提交的教程（增删改、切换搜索模式） |
| `/设定提交` | 向社区设定知识库贡献条目（公开投票审核） |
| `/设定说明` | 查看社区设定类别说明 |
| `/聊天设置` | 聊天开关、冷却、AI Provider/模型、工具设置 |
| `/admin` | 管理面板：知识条目编辑、向量库元数据、对话记忆 |
| `@Bot 提问` | 触发 AI 回复（Agentic 检索 + 工具调用） |

## 项目结构

```
src/
├── main.py                  # 启动入口：初始化数据库/AI工具/Cog自动扫描加载
├── config.py                # 全局配置 + bot.yaml 身份加载
├── database/                # SQLAlchemy async + ORM 模型（按 schema 划分）
└── chat/
    ├── cogs/                # AI对话、黑名单管理、论坛事件分发等 Cog
    ├── config/              # chat_config / prompts（人设）/ emoji_config
    ├── services/            # 跨 feature 服务：prompt构建、审核、个人记忆…
    │   └── ai/              # 多 Provider 调度、故障转移、工具循环
    ├── features/
    │   ├── community_settings/  # 社区设定（原世界书）：检索/向量化/贡献
    │   ├── tutorial_search/     # 教程库：RAG索引/检索/UGC管理UI
    │   ├── forum_search/        # 论坛帖同步与混合检索
    │   ├── personal_memory/     # 个人记忆：对话块/记忆笔记
    │   ├── tools/functions/     # AI 工具（Pydantic 自动 Schema）
    │   ├── admin_panel/         # /admin 管理面板
    │   ├── chat_settings/       # /聊天设置
    │   ├── tarot/ web_search/ content_filter/ channel_mute/
    └── utils/database.py    # Bot 运行时数据（bot schema，PG 版）
alembic/versions/            # 初始基线迁移（0001_initial_baseline）
scripts/init_db.py           # 一键初始化数据库
config/bot.yaml              # Bot 身份配置（名字/社区名，改这里即全局换身份）
```

## 数据库说明

- 全部数据在 **PostgreSQL**，按 schema 划分：`tutorials` / `community_settings` / `community` / `forum` / `conversation` / `bot` / `user` / `ai_config` / `content_filter`
- 混合检索依赖 ParadeDB 的 **BM25 索引**（`pdb.chinese_compatible` 中文分词）与 **pgvector HNSW 索引**（`halfvec_cosine_ops`），由 `scripts/init_db.py` 或初始迁移创建
- 向量列为双向量设计（`bge_embedding` / `qwen_embedding` 各 halfvec(1024)），切换 embedding 模型不丢旧数据
- 运行时配置（embedding 模型选择、工具禁用、黑名单、冷却）存于 `bot` schema，由 `chat_db_manager` 管理

## 身份定制

Bot 名称、社区名、人设风格全部集中在 `config/bot.yaml`，改配置即全局换身份，代码零硬编码。
