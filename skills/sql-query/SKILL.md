---
name: sql-query
display_name: SQL 查询技能
description: 数据库表结构速查与高效查询模板，写 SQL 前先阅读本技能
injection_mode: prompt
enabled: true
---

# SQL 查询技能

执行任何 SQL 前，先阅读本速查表；**表结构以本表为准，不要凭猜测编写列名**。
覆盖不了的结构，先查 `information_schema.columns` 再写正式查询。

## 数据库速查表（PostgreSQL，单库多 schema）

### 用户与记忆
| 表 | 关键列 | 说明 |
|---|---|---|
| `community.member_profiles` | id, discord_id(唯一), title, full_text, personal_summary, personal_message_count, **history(JSON)** | 用户档案（首次对话自动建档） |
| `conversation.conversation_blocks` | id, discord_id, conversation_text, start_time, end_time, message_count | 已打包对话记忆块（10 条消息/块） |
| `user.user_memory_notes` | **user_id(=Discord ID 字符串，非 discord_id)**, category(emotion/status/preference/positive_event), content(≤150字) | AI 记忆笔记 |

### ⭐ 用户聊天记录：member_profiles.history 字段（JSON）

`history` 存储**未打包的近期对话轮次**（与 conversation_blocks 的已打包记录互补——查全部聊天记录需两者都查）。

- 存储格式：JSON 数组，元素结构 `{"role": "user"|"model", "parts": ["文本"], "timestamp": "ISO时间"}`
  - ⚠ 角色名是 **`model`**（Gemini 风格），**不是** `assistant`
  - ⚠ 文本在 `parts` **数组**里，取第一个：`elem->'parts'->>0`
  - ⚠ 旧数据 timestamp 可能无时区后缀（naive，按 UTC 理解）
- 查询需把 JSON 列转换为 jsonb 再展开：`history::jsonb`

**模板：展开全部用户的每条聊天消息**
```sql
SELECT p.discord_id,
       elem->>'timestamp'                          AS ts,
       elem->>'role'                               AS role,
       elem->'parts'->>0                           AS text
FROM community.member_profiles p,
     jsonb_array_elements(p.history::jsonb) AS elem
ORDER BY p.discord_id, elem->>'timestamp';
```

**模板：全库搜索聊天内容包含关键词（含已打包与未打包）**
```sql
-- 未打包（history JSON 展开）
SELECT p.discord_id, elem->>'timestamp' AS ts, elem->'parts'->>0 AS text
FROM community.member_profiles p,
     jsonb_array_elements(p.history::jsonb) AS elem
WHERE elem->'parts'->>0 ILIKE '%关键词%'
UNION ALL
SELECT discord_id::text, NULL, left(conversation_text, 200)
FROM conversation.conversation_blocks
WHERE conversation_text ILIKE '%关键词%'
LIMIT 50;
```

### 知识库
| 表 | 关键列 | 说明 |
|---|---|---|
| `tutorials.tutorial_documents` | id, title, author_id, original_content | 教程原文 |
| `tutorials.knowledge_chunks` | id, document_id, chunk_text, parent_id(父块), section_path | 教程分块 |
| `community_settings.documents` | id, external_id, title, full_text | 社区设定条目 |
| `community_settings.chunks` | id, document_id, chunk_text | 设定分块 |
| `forum.forum_threads` | id, thread_id, thread_name, content, author_id, category_name | 论坛帖整帖索引 |

### 运行时与统计
| 表 | 关键列 | 说明 |
|---|---|---|
| `bot.web_chat_messages` | id, role(user/assistant), content, prompt_tokens, completion_tokens, created_at | Web 问答记录 |
| `bot.ai_model_usage` | model_name, provider_name, usage_count | 模型累计调用 |
| `bot.daily_stats` | stat_date, issue_user_warning_count, tarot_reading_count, forum_search_count | 功能每日统计 |
| `bot.blacklisted_users` | user_id, guild_id, expires_at | 服务器黑名单 |
| `ai_config.ai_models` | model_name, provider_id, supports_vision, enabled | AI 模型配置 |
| `ai_config.ai_providers` | name, provider_type, base_url | Provider 配置 |

## 高频查询模板

```sql
-- 各 schema 表数量
SELECT table_schema, count(*) FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog','information_schema')
GROUP BY table_schema ORDER BY 1;

-- 某表全部列结构（不确定列名时先执行这个）
SELECT table_schema, table_name, column_name, data_type
FROM information_schema.columns WHERE table_name = '表名';

-- 用户消息量排行
SELECT discord_id, personal_message_count FROM community.member_profiles
ORDER BY personal_message_count DESC LIMIT 10;

-- 某用户全部聊天（已打包块）
SELECT id, start_time, left(conversation_text, 200) FROM conversation.conversation_blocks
WHERE discord_id = 'Discord ID' ORDER BY start_time DESC;

-- 最近 Web 问答
SELECT created_at, role, left(content, 80) FROM bot.web_chat_messages
ORDER BY id DESC LIMIT 10;
```

## 注意事项

1. 本工具为**只读**（仅 SELECT/WITH/SHOW/EXPLAIN），写操作会被拒绝
2. 无 LIMIT 的查询自动限制 50 行，统计类请显式写 `count(*)`
3. `user` 是 PostgreSQL 保留字，引用该 schema 的表务必写 `"user".表名`
4. 时间列均为 timestamptz（UTC），日期统计用 `created_at AT TIME ZONE 'Asia/Shanghai'::date` 转北京时间
