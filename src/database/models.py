import datetime
from typing import Optional
from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    DateTime,
    Date,
    ForeignKey,
    JSON,
    Index,
    func,
)
from sqlalchemy.orm import declarative_base, relationship, Mapped, mapped_column
from pgvector.sqlalchemy import HALFVEC

# --- 全局配置 ---
EMBEDDING_DIMENSION = 1024  # bge-m3 和 qwen3-embedding-0.6B 模型都使用 1024 维度
QWEN_EMBEDDING_DIMENSION = 1024  # qwen3-embedding-0.6B 模型的维度

# --- Schema 名称 ---
TUTORIALS_SCHEMA = "tutorials"
COMMUNITY_SETTINGS_SCHEMA = "community_settings"
COMMUNITY_SCHEMA = "community"
USER_SCHEMA = "user"
FORUM_SCHEMA = "forum"
CONVERSATION_SCHEMA = "conversation"
AI_CONFIG_SCHEMA = "ai_config"
CONTENT_FILTER_SCHEMA = "content_filter"
BOT_SCHEMA = "bot"

Base = declarative_base()


class TutorialDocument(Base):
    """
    代表一份原始、完整的教程文档。
    该表存储了源信息和元数据。
    """

    __tablename__ = "tutorial_documents"
    __table_args__ = {"schema": TUTORIALS_SCHEMA}

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, comment="教程的标题。")
    category = Column(String, nullable=True, comment="教程所属的高级类别。")
    source_url = Column(String, nullable=True, comment="文档的源URL。")
    author = Column(String, nullable=True, comment="文档的作者名。")
    author_id = Column(String, nullable=False, comment="作者的Discord用户ID。")
    thread_id = Column(String, nullable=True, comment="原始Discord帖子的ID。")
    tags = Column(JSON, nullable=True, comment="用于存储标签的JSON字段。")

    # 完整的原始内容存储在这里，以备参考和重新分块。
    original_content = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 这创建了与 KnowledgeChunk 的一对多关系。
    chunks = relationship("KnowledgeChunk", back_populates="document")

    __table_args__ = (
        Index("ix_tutorial_documents_author_id", "author_id"),
        {"schema": TUTORIALS_SCHEMA},
    )

    def __repr__(self):
        return f"<TutorialDocument(id={self.id}, title='{self.title}')>"


class KnowledgeChunk(Base):
    """
    代表来自 TutorialDocument 的一个文本块，及其对应的向量。
    我们将在此表上执行向量搜索。
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        # 警告：下面的 BM25 索引定义仅供参考，因为它无法完全表达 ParadeDB v2 所需的特殊原生 SQL 语法。
        # 该索引的实际创建和管理是在 Alembic 迁移脚本 '43ecab4319d0' 中通过 op.execute() 手动完成的。
        # Index(
        #     "idx_chunk_text_bm25",
        #     "chunk_text",
        #     postgresql_using="bm25",
        # ),
        # HNSW 索引定义现在是准确的，包含了 pgvector 必需的操作符类。
        Index(
            "idx_bge_embedding_hnsw",
            "bge_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"bge_embedding": "halfvec_cosine_ops"},
        ),
        Index(
            "idx_qwen_embedding_hnsw",
            "qwen_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"qwen_embedding": "halfvec_cosine_ops"},
        ),
        {"schema": TUTORIALS_SCHEMA},
    )

    id = Column(Integer, primary_key=True, index=True)

    # 用于链接回父文档的外键。
    document_id = Column(
        Integer, ForeignKey(f"{TUTORIALS_SCHEMA}.tutorial_documents.id"), nullable=False
    )

    chunk_text = Column(Text, nullable=False, comment="这个特定文本块的内容。")
    chunk_order = Column(Integer, nullable=False, comment="文本块在文档中的序列号。")
    parent_id = Column(
        Integer,
        ForeignKey(f"{TUTORIALS_SCHEMA}.knowledge_chunks.id"),
        nullable=True,
        comment="父块ID（节级父块）；NULL=父块或独立单块。父块不建向量，仅用于检索后回取节级上下文（small-to-big）。",
    )
    section_path = Column(
        Text,
        nullable=True,
        comment="面包屑章节路径，如 '第二章 部署 > 2.1 环境准备'（源自上传文档的 Markdown 标题层级）",
    )

    bge_embedding = Column(
        HALFVEC(EMBEDDING_DIMENSION),
        nullable=True,
        comment="BGE-M3 模型的嵌入向量。仅在使用 BGE 模型的向量模式下写入。",
    )
    qwen_embedding = Column(
        HALFVEC(QWEN_EMBEDDING_DIMENSION),
        nullable=True,
        comment="Qwen3-Embedding-0.6B 模型的嵌入向量。",
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    # 这创建了回到 TutorialDocument 的多对一关系。
    document = relationship("TutorialDocument", back_populates="chunks")

    def __repr__(self):
        return f"<KnowledgeChunk(id={self.id}, document_id={self.document_id})>"


class ThreadSetting(Base):
    """
    存储每个帖子（Thread）的独立设置。
    例如：教程搜索模式（ISOLATED 或 PRIORITY）。
    """

    __tablename__ = "thread_settings"
    __table_args__ = {"schema": TUTORIALS_SCHEMA}

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String, unique=True, nullable=False, comment="Discord帖子的ID")
    search_mode = Column(
        String,
        nullable=False,
        default="ISOLATED",
        comment="教程搜索模式: 'ISOLATED' (隔离) 或 'PRIORITY' (优先)",
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<ThreadSetting(thread_id='{self.thread_id}', search_mode='{self.search_mode}')>"


# --- 社区设定模型 (关联表结构) ---


class CommunitySettingDocument(Base):
    """
    代表一份完整的社区设定条目文档。
    存储源信息和元数据，与分块建立一对多关系。
    """

    __tablename__ = "documents"
    __table_args__ = {"schema": COMMUNITY_SETTINGS_SCHEMA}

    id = Column(Integer, primary_key=True)
    external_id = Column(
        String, unique=True, nullable=False, comment="来源系统的唯一ID"
    )
    title = Column(Text, nullable=True)
    full_text = Column(
        Text, nullable=False, comment="完整的文本内容，用于重新分块和BM25搜索"
    )
    source_metadata = Column(JSON, nullable=True, comment="完整元数据备份")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 与分块的一对多关系
    chunks = relationship(
        "CommunitySettingChunk", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<CommunitySettingDocument(id={self.id}, title='{self.title}')>"


class CommunitySettingChunk(Base):
    """
    代表来自 CommunitySettingDocument 的一个文本块，及其对应的向量。
    我们将在此表上执行向量搜索。
    """

    __tablename__ = "chunks"
    __table_args__ = (
        # HNSW 索引用于向量搜索
        Index(
            "idx_cs_bge_embedding_hnsw",
            "bge_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"bge_embedding": "halfvec_cosine_ops"},
        ),
        Index(
            "idx_cs_qwen_embedding_hnsw",
            "qwen_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"qwen_embedding": "halfvec_cosine_ops"},
        ),
        {"schema": COMMUNITY_SETTINGS_SCHEMA},
    )

    id = Column(Integer, primary_key=True)

    # 链接回父文档的外键
    document_id = Column(
        Integer,
        ForeignKey(f"{COMMUNITY_SETTINGS_SCHEMA}.documents.id"),
        nullable=False,
    )

    chunk_index = Column(Integer, nullable=False, comment="分块在文档中的序号")
    chunk_text = Column(Text, nullable=False, comment="这个特定文本块的内容")

    bge_embedding = Column(
        HALFVEC(EMBEDDING_DIMENSION),
        nullable=True,
        comment="BGE-M3 模型的嵌入向量。",
    )
    qwen_embedding = Column(
        HALFVEC(QWEN_EMBEDDING_DIMENSION),
        nullable=True,
        comment="Qwen3-Embedding-0.6B 模型的嵌入向量。",
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 回到 CommunitySettingDocument 的多对一关系
    document = relationship("CommunitySettingDocument", back_populates="chunks")

    def __repr__(self):
        return f"<CommunitySettingChunk(id={self.id}, document_id={self.document_id}, chunk_index={self.chunk_index})>"


class CommunitySettingPendingEntry(Base):
    """
    社区设定的待审核条目队列。
    普通用户提交后进入公开投票审核，管理员可直接批准。
    """

    __tablename__ = "pending_entries"
    __table_args__ = (
        Index("ix_cs_pending_status_expires", "status", "expires_at"),
        {"schema": COMMUNITY_SETTINGS_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    entry_type = Column(
        String(50), nullable=False, default="community_setting", comment="条目类型"
    )
    data_json = Column(JSON, nullable=False, comment="提交的原始数据")
    message_id = Column(
        BigInteger, nullable=False, default=-1, comment="审核投票消息的ID"
    )
    channel_id = Column(BigInteger, nullable=False, comment="提交所在频道ID")
    guild_id = Column(BigInteger, nullable=False, comment="提交所在服务器ID")
    proposer_id = Column(BigInteger, nullable=False, comment="提交者Discord ID")
    status = Column(
        String(20), nullable=False, default="pending", comment="pending/approved/rejected"
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False, comment="审核过期时间")

    def __repr__(self):
        return f"<CommunitySettingPendingEntry(id={self.id}, status='{self.status}')>"


# --- 社区成员模型 (关联表结构) ---


class CommunityMemberProfile(Base):
    """
    代表一个社区成员的完整档案。
    存储成员元数据，与分块建立一对多关系。
    """

    __tablename__ = "member_profiles"
    __table_args__ = {"schema": COMMUNITY_SCHEMA}

    id = Column(Integer, primary_key=True)
    external_id = Column(
        String,
        unique=True,
        nullable=False,
        comment="来自旧系统的唯一ID, 例如 member_id",
    )
    discord_id = Column(
        String, unique=True, nullable=True, comment="成员的Discord数字ID"
    )
    title = Column(Text, nullable=True, comment="成员标题/昵称")
    full_text = Column(
        Text,
        nullable=False,
        comment="完整的成员档案文本，用于重新分块和BM25搜索",
    )
    source_metadata = Column(JSON, nullable=True, comment="存储原始的、完整的成员档案")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    personal_summary = Column(Text, nullable=True, comment="个人记忆")
    history = Column(JSON, nullable=True, comment="用于生成最近一次个人记忆")
    personal_message_count = Column(
        Integer, nullable=False, default=0, server_default="0", comment="个人消息计数"
    )

    def __repr__(self):
        return f"<CommunityMemberProfile(id={self.id}, discord_id='{self.discord_id}')>"



class TokenUsage(Base):
    """
    记录每天的Token使用情况。
    """

    __tablename__ = "token_usage"
    __table_args__ = {"schema": BOT_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    date: Mapped[datetime.date] = mapped_column(
        Date, nullable=False, unique=True, default=datetime.date.today
    )
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    call_count: Mapped[int] = mapped_column(default=0)

    def __repr__(self):
        return f"<TokenUsage(date={self.date}, total_tokens={self.total_tokens})>"


# --- 用户设置模型 (PostgreSQL) ---


class UserToolSettings(Base):
    """
    存储每个用户的工具启用设置。
    用户可以控制在自己的帖子里Bot可以使用哪些工具。
    默认启用所有工具，如果用户没有设置记录。
    """

    __tablename__ = "user_tool_settings"
    __table_args__ = {"schema": USER_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="用户的Discord ID"
    )
    enabled_tools: Mapped[dict] = mapped_column(
        JSON,
        nullable=True,
        comment="用户启用的工具列表（JSON格式），为null表示启用所有工具",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<UserToolSettings(user_id='{self.user_id}')>"


class UserCommandSettings(Base):
    """
    存储每个用户的命令启用设置。
    用户可以控制在自己的帖子里哪些命令可以使用。
    默认启用所有命令，如果用户没有设置记录。
    """

    __tablename__ = "user_command_settings"
    __table_args__ = {"schema": USER_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="用户的Discord ID"
    )
    enabled_commands: Mapped[dict] = mapped_column(
        JSON,
        nullable=True,
        comment="用户启用的命令列表（JSON格式），为null表示启用所有命令",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<UserCommandSettings(user_id='{self.user_id}')>"


class UserPersonaPreference(Base):
    """
    存储每个用户对Bot人设风格的偏好。
    用户可以选择Bot的对话风格，如默认风格或温柔风格。
    """

    __tablename__ = "user_persona_preference"
    __table_args__ = {"schema": USER_SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, comment="用户的Discord ID"
    )
    persona_style: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="default",
        comment="人设风格: default(默认) | gentle(温柔)",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<UserPersonaPreference(user_id='{self.user_id}', style='{self.persona_style}')>"


class UserMemoryNote(Base):
    """
    存储AI对用户的结构化记忆笔记。
    由AI通过 manage_memory 工具写入，自动注入到对话上下文中。
    仅对持有名片（profile）的用户生效。
    """

    __tablename__ = "user_memory_notes"
    __table_args__ = (
        Index("ix_user_memory_notes_user_category", "user_id", "category"),
        {"schema": USER_SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True, comment="用户的Discord ID"
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="记忆类别: emotion(情感) / status(状态) / preference(偏好) / positive_event(正面事件)",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="记忆内容（单条不超过150字）"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<UserMemoryNote(id={self.id}, user_id='{self.user_id}', category='{self.category}')>"


# --- 论坛搜索模型 (ParadeDB) ---


class ForumThread(Base):
    """
    代表一个完整的论坛帖子。
    使用单表结构，不进行文本分块，支持混合搜索（向量+BM25）。
    """

    __tablename__ = "forum_threads"
    __table_args__ = (
        # HNSW 向量索引用于向量相似度搜索
        Index(
            "idx_forum_bge_embedding_hnsw",
            "bge_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"bge_embedding": "halfvec_cosine_ops"},
        ),
        Index(
            "idx_forum_qwen_embedding_hnsw",
            "qwen_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"qwen_embedding": "halfvec_cosine_ops"},
        ),
        # 创建时间索引用于排序
        Index("idx_forum_created_at", "created_at"),
        # 分类名称索引用于过滤
        Index("idx_forum_category", "category_name"),
        # 作者ID索引用于过滤
        Index("idx_forum_author", "author_id"),
        # 频道ID索引用于过滤
        Index("idx_forum_channel", "channel_id"),
        {"schema": FORUM_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Discord 帖子唯一标识
    thread_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, comment="Discord帖子的唯一ID"
    )

    # 帖子基本信息
    thread_name: Mapped[str] = mapped_column(Text, nullable=False, comment="帖子标题")
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="帖子完整内容（首楼）"
    )

    # 作者信息
    author_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="作者的Discord ID"
    )
    author_name: Mapped[str] = mapped_column(
        Text, nullable=False, comment="作者的显示名称"
    )

    # 分类和频道信息
    category_name: Mapped[str] = mapped_column(
        Text, nullable=False, comment="论坛频道名称（分类）"
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="父频道的Discord ID"
    )
    guild_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="服务器的Discord ID"
    )

    # 时间戳
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="帖子创建时间（Discord时间）"
    )

    # 可选字段
    source_metadata: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="来自旧系统的完整元数据备份"
    )
    bge_embedding: Mapped[list[float] | None] = mapped_column(
        HALFVEC(EMBEDDING_DIMENSION),
        nullable=True,
        comment="BGE-M3 模型的整帖内容向量嵌入（用于语义搜索）",
    )
    qwen_embedding: Mapped[list[float] | None] = mapped_column(
        HALFVEC(QWEN_EMBEDDING_DIMENSION),
        nullable=True,
        comment="Qwen3-Embedding-0.6B 模型的整帖内容向量嵌入（用于语义搜索）",
    )

    # 数据库管理时间戳
    created_at_db: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="数据库记录创建时间"
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="数据库记录更新时间",
    )

    def __repr__(self):
        return f"<ForumThread(id={self.id}, thread_id={self.thread_id}, thread_name='{self.thread_name}')>"


class ForumProcessedThread(Base):
    """已索引的论坛帖子ID（避免重复处理，原 forum_sync_status.db 迁移）"""

    __tablename__ = "processed_threads"
    __table_args__ = {"schema": FORUM_SCHEMA}

    thread_id = Column(BigInteger, primary_key=True)

    def __repr__(self):
        return f"<ForumProcessedThread(thread_id={self.thread_id})>"


class ForumBackfillStatus(Base):
    """每个论坛频道的历史回溯进度书签"""

    __tablename__ = "backfill_status"
    __table_args__ = {"schema": FORUM_SCHEMA}

    channel_id = Column(BigInteger, primary_key=True)
    oldest_known_timestamp = Column(String(50), nullable=True)
    is_complete = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<ForumBackfillStatus(channel_id={self.channel_id}, complete={self.is_complete})>"


# --- 对话记忆块模型 (ParadeDB) ---


# 对话记忆使用的 schema 见顶部 CONVERSATION_SCHEMA


class ConversationBlock(Base):
    """
    代表用户与Bot的一段对话块。
    每 block_size 条对话存储为一个块，支持向量检索实现"永久记忆"。
    使用混合搜索（向量+BM25）来检索相关历史对话。
    """

    __tablename__ = "conversation_blocks"
    __table_args__ = (
        # HNSW 向量索引用于向量相似度搜索
        Index(
            "idx_conv_bge_embedding_hnsw",
            "bge_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"bge_embedding": "halfvec_cosine_ops"},
        ),
        Index(
            "idx_conv_qwen_embedding_hnsw",
            "qwen_embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"qwen_embedding": "halfvec_cosine_ops"},
        ),
        # 用户ID索引用于过滤
        Index("idx_conv_discord_id", "discord_id"),
        # 开始时间索引用于排序
        Index("idx_conv_start_time", "start_time"),
        {"schema": CONVERSATION_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 用户标识
    discord_id: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="用户的Discord ID"
    )

    # 对话块内容
    conversation_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="对话块的原始文本内容"
    )

    # 时间范围（用于显示"X天前的对话"）
    start_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="对话块中第一条消息的时间"
    )
    end_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="对话块中最后一条消息的时间"
    )

    # 消息数量
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="对话块中的消息数量"
    )

    # --- [DISABLED] 印象总结功能（flash模型）已禁用 ---
    # summarized: Mapped[bool] = mapped_column(
    #     Integer,  # SQLite兼容：用 0/1 表示布尔值
    #     nullable=False,
    #     default=0,
    #     comment="是否已被印象总结（0=未总结，1=已总结）",
    # )

    # 向量嵌入
    bge_embedding: Mapped[list[float] | None] = mapped_column(
        HALFVEC(EMBEDDING_DIMENSION),
        nullable=True,
        comment="BGE-M3 模型的对话内容向量嵌入（用于语义搜索）",
    )
    qwen_embedding: Mapped[list[float] | None] = mapped_column(
        HALFVEC(QWEN_EMBEDDING_DIMENSION),
        nullable=True,
        comment="Qwen3-Embedding-0.6B 模型的对话内容向量嵌入（用于语义搜索）",
    )

    # 数据库管理时间戳
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="数据库记录创建时间"
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="数据库记录更新时间",
    )

    def __repr__(self):
        return f"<ConversationBlock(id={self.id}, discord_id='{self.discord_id}', start_time={self.start_time})>"


# --- User 扩展模型 (ParadeDB) ---


class UserWarningRecord(Base):
    __tablename__ = "user_warnings"
    __table_args__ = (
        Index("ix_warnings_user_guild", "user_id", "guild_id", unique=True),
        {"schema": USER_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    guild_id: Mapped[str] = mapped_column(String(50), nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return (
            f"<UserWarningRecord(user_id='{self.user_id}', guild_id='{self.guild_id}')>"
        )


# --- AI Provider / Model 配置模型 (PostgreSQL) ---


class AiProvider(Base):
    """
    AI 服务提供商配置表。
    通过 Discord UI 动态管理，API Key 加密存储。
    """

    __tablename__ = "ai_providers"
    __table_args__ = (
        Index("ix_ai_provider_name", "name", unique=True),
        {"schema": AI_CONFIG_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, comment="Provider 唯一标识名称"
    )
    provider_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Provider 类型: gemini / deepseek / openai_compatible",
    )
    display_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="显示名称"
    )
    api_key_encrypted: Mapped[str] = mapped_column(
        Text, nullable=False, comment="加密后的 API Key"
    )
    base_url: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="API 基础 URL"
    )
    extra: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="额外配置参数 (JSON)"
    )
    enabled: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=1, comment="是否启用 (1=启用, 0=禁用)"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    models: Mapped[list["AiModel"]] = relationship(
        "AiModel", back_populates="provider", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<AiProvider(name='{self.name}', type='{self.provider_type}')>"


class AiModel(Base):
    """
    AI 模型配置表。
    每个 Model 关联到一个 AiProvider，通过 Discord UI 动态管理。
    """

    __tablename__ = "ai_models"
    __table_args__ = (
        Index("ix_ai_model_name", "model_name", unique=True),
        Index("ix_ai_model_provider", "provider_id"),
        {"schema": AI_CONFIG_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_name: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, comment="模型唯一标识 (如 deepseek-chat)"
    )
    display_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="模型显示名称"
    )
    provider_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{AI_CONFIG_SCHEMA}.ai_providers.id"),
        nullable=False,
        comment="所属 Provider ID",
    )
    actual_model: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="实际调用的模型名称"
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="模型描述"
    )
    supports_vision: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=0, comment="是否支持视觉"
    )
    supports_tools: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=1, comment="是否支持工具调用"
    )
    supports_thinking: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=0, comment="是否支持思考模式"
    )
    max_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=8192, comment="最大输出 token 数"
    )
    generation_config: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="生成参数配置 (temperature, top_p 等)"
    )
    prompt_config: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="提示词配置"
    )
    enabled: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=1, comment="是否启用 (1=启用, 0=禁用)"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    provider: Mapped["AiProvider"] = relationship("AiProvider", back_populates="models")

    def __repr__(self):
        return f"<AiModel(model_name='{self.model_name}', provider_id={self.provider_id})>"


# --- 内容过滤关键词模型 (PostgreSQL) ---


class ContentFilterKeyword(Base):
    __tablename__ = "content_filter_keywords"
    __table_args__ = (
        Index("ix_cf_keyword_unique", "keyword", unique=True),
        Index("ix_cf_keyword_ignored", "is_ignored"),
        {"schema": CONTENT_FILTER_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    keyword: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    is_ignored: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=0
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self):
        return f"<ContentFilterKeyword(keyword='{self.keyword}', is_ignored={self.is_ignored})>"


# --- Bot 运行时数据模型（原遗留 SQLite chat.db 迁移至 PostgreSQL） ---


class GlobalSetting(Base):
    """全局键值设置（embedding 模型选择、工具禁用列表等）"""

    __tablename__ = "global_settings"
    __table_args__ = {"schema": BOT_SCHEMA}

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<GlobalSetting(key='{self.key}')>"


class BlacklistedUser(Base):
    """服务器级黑名单（警告系统自动加入）"""

    __tablename__ = "blacklisted_users"
    __table_args__ = {"schema": BOT_SCHEMA}

    user_id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger, primary_key=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    def __repr__(self):
        return f"<BlacklistedUser(user_id={self.user_id}, guild_id={self.guild_id})>"


class GloballyBlacklistedUser(Base):
    """全局黑名单"""

    __tablename__ = "globally_blacklisted_users"
    __table_args__ = {"schema": BOT_SCHEMA}

    user_id = Column(BigInteger, primary_key=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    def __repr__(self):
        return f"<GloballyBlacklistedUser(user_id={self.user_id})>"


class GlobalChatConfig(Base):
    """服务器级聊天配置（总开关、故障降级开关）"""

    __tablename__ = "global_chat_config"
    __table_args__ = {"schema": BOT_SCHEMA}

    guild_id = Column(BigInteger, primary_key=True)
    chat_enabled = Column(Integer, nullable=False, default=1)
    api_fallback_enabled = Column(Integer, nullable=False, default=1)

    def __repr__(self):
        return f"<GlobalChatConfig(guild_id={self.guild_id})>"


class ChannelChatConfig(Base):
    """频道/分类级聊天配置（继承自全局，可覆盖开关与冷却）"""

    __tablename__ = "channel_chat_config"
    __table_args__ = (
        Index("uq_channel_config_entity", "guild_id", "entity_id", unique=True),
        {"schema": BOT_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    entity_id = Column(BigInteger, nullable=False, comment="频道ID或分类ID")
    entity_type = Column(String(20), nullable=False, comment="'channel' 或 'category'")
    is_chat_enabled = Column(Integer, nullable=True, comment="可空，为空则继承上级")
    cooldown_seconds = Column(Integer, nullable=True)
    cooldown_duration = Column(Integer, nullable=True)
    cooldown_limit = Column(Integer, nullable=True)

    def __repr__(self):
        return f"<ChannelChatConfig(guild_id={self.guild_id}, entity_id={self.entity_id})>"


class UserChannelTimestamp(Base):
    """频率限制：用户在频道内的消息时间戳"""

    __tablename__ = "user_channel_timestamps"
    __table_args__ = (
        Index("idx_user_channel_ts", "user_id", "channel_id", "timestamp"),
        {"schema": BOT_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    channel_id = Column(BigInteger, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<UserChannelTimestamp(user_id={self.user_id}, channel_id={self.channel_id})>"


class UserChannelCooldown(Base):
    """固定时长冷却：用户在频道内的最后消息时间"""

    __tablename__ = "user_channel_cooldown"
    __table_args__ = {"schema": BOT_SCHEMA}

    user_id = Column(BigInteger, primary_key=True)
    channel_id = Column(BigInteger, primary_key=True)
    last_message_timestamp = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<UserChannelCooldown(user_id={self.user_id}, channel_id={self.channel_id})>"


class MutedChannel(Base):
    """被投票禁言的频道"""

    __tablename__ = "muted_channels"
    __table_args__ = {"schema": BOT_SCHEMA}

    channel_id = Column(BigInteger, primary_key=True)
    muted_at = Column(DateTime(timezone=True), server_default=func.now())
    muted_until = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<MutedChannel(channel_id={self.channel_id})>"


class AiPrompt(Base):
    """每服务器的自定义提示词模板"""

    __tablename__ = "ai_prompts"
    __table_args__ = (
        Index("uq_ai_prompts_guild_name", "guild_id", "prompt_name", unique=True),
        {"schema": BOT_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    prompt_name = Column(String(100), nullable=False)
    prompt_content = Column(Text, nullable=False)
    is_active = Column(Integer, nullable=False, default=1)

    def __repr__(self):
        return f"<AiPrompt(guild_id={self.guild_id}, name='{self.prompt_name}')>"


class ChannelMemoryAnchor(Base):
    """频道记忆锚点（频道上下文检索的起点消息）"""

    __tablename__ = "channel_memory_anchors"
    __table_args__ = {"schema": BOT_SCHEMA}

    guild_id = Column(BigInteger, primary_key=True)
    channel_id = Column(BigInteger, primary_key=True)
    anchor_message_id = Column(BigInteger, nullable=False)

    def __repr__(self):
        return f"<ChannelMemoryAnchor(channel_id={self.channel_id})>"


class ModelUsage(Base):
    """模型累计使用计数"""

    __tablename__ = "ai_model_usage"
    __table_args__ = {"schema": BOT_SCHEMA}

    model_name = Column(String(200), primary_key=True)
    provider_name = Column(String(100), nullable=True)
    usage_count = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<ModelUsage(model='{self.model_name}', count={self.usage_count})>"


class DailyModelUsage(Base):
    """模型每日使用计数（北京时间）"""

    __tablename__ = "daily_model_usage"
    __table_args__ = {"schema": BOT_SCHEMA}

    model_name = Column(String(200), primary_key=True)
    usage_date = Column(String(10), primary_key=True, comment="北京时间日期 YYYY-MM-DD")
    provider_name = Column(String(100), nullable=True)
    usage_count = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<DailyModelUsage(model='{self.model_name}', date='{self.usage_date}')>"


class DailyStat(Base):
    """功能每日使用统计"""

    __tablename__ = "daily_stats"
    __table_args__ = {"schema": BOT_SCHEMA}

    stat_date = Column(String(10), primary_key=True, comment="北京时间日期 YYYY-MM-DD")
    issue_user_warning_count = Column(Integer, nullable=False, default=0)
    tarot_reading_count = Column(Integer, nullable=False, default=0)
    forum_search_count = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<DailyStat(date='{self.stat_date}')>"


class BotPersona(Base):
    """Bot 人设库（后台可编辑，替代 prompts.py 硬编码的编辑入口）"""

    __tablename__ = "bot_persona"
    __table_args__ = (
        Index("ix_bot_persona_name", "name", unique=True),
        {"schema": AI_CONFIG_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False, comment="人设唯一标识（default/gentle/自定义，对应 persona_style）")
    display_name = Column(String(100), nullable=False, comment="后台展示名")
    system_prompt = Column(Text, nullable=False, comment="完整人设正文（<character> 结构）")
    is_default = Column(Integer, nullable=False, default=0, comment="1=默认人设（无用户偏好时使用）")
    enabled = Column(Integer, nullable=False, default=1, comment="1=启用")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<BotPersona(name='{self.name}', is_default={self.is_default})>"


class WebChatMessage(Base):
    """Web 问答演示的聊天记录持久化（每行一条 user/assistant 消息）"""

    __tablename__ = "web_chat_messages"
    __table_args__ = (
        Index("ix_web_chat_created_at", "created_at"),
        {"schema": BOT_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    role = Column(String(20), nullable=False, comment="user / assistant")
    content = Column(Text, nullable=False, comment="消息正文")
    reasoning = Column(Text, nullable=True, comment="思维链全文（多轮以分隔符拼接；历史展示用）")
    tool_trace = Column(JSON, nullable=True, comment="assistant 消息关联的工具调用轨迹")
    model = Column(String(200), nullable=True, comment="生成使用的模型")
    elapsed_ms = Column(Integer, nullable=True, comment="总耗时（毫秒）")
    prompt_tokens = Column(Integer, nullable=True, comment="输入 token 数（来自 API usage）")
    completion_tokens = Column(Integer, nullable=True, comment="输出 token 数（来自 API usage）")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<WebChatMessage(id={self.id}, role='{self.role}')>"
