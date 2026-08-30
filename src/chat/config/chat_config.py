# -*- coding: utf-8 -*-

"""
存储 Chat 模块相关的非敏感、硬编码的常量。
"""

import os
from typing import Literal
from src.config import _parse_ids

# --- Chat 功能总开关 ---
CHAT_ENABLED = os.getenv("CHAT_ENABLED", "False").lower() == "true"

# --- 向量模式配置 ---
# 支持三种模式:
# - "none": 无向量，直接聊天（不使用 RAG 检索功能）
# - "api": API 向量，使用 Gemini Embedding API
# - "local": 本地向量，使用 Ollama 本地模型（默认）
VectorMode = Literal["none", "api", "local"]
VECTOR_MODE: VectorMode = os.getenv("VECTOR_MODE", "local").lower()  # type: ignore

# --- 交互禁用配置 ---
# 在这些频道ID中，所有交互（包括 @mention 和 /命令）都将被完全禁用。
# 示例: DISABLED_INTERACTION_CHANNEL_IDS = [123456789012345678, 987654321098765432]
DISABLED_INTERACTION_CHANNEL_IDS = [
    1393179379126767686,
    1307242450300964986,
    1234431470773338143,
]

# --- 限制豁免频道 ---
# 在这些频道ID中，“长回复私聊”、“闭嘴命令”和“忏悔内容不可见”的限制将无效。
UNRESTRICTED_CHANNEL_IDS = _parse_ids("UNRESTRICTED_CHANNEL_IDS")

# --- 工具加载器配置 ---
# 注意：工具的启用/禁用状态现在由 GlobalToolSettingsService 在运行时控制。
# 管理员可以通过 /聊天设置 命令中的"全局工具设置"按钮来配置。
#
# 旧配置（已废弃）：
# DISABLED_TOOLS - 禁用的工具模块列表（文件名，不含.py扩展名）
# HIDDEN_TOOLS - 隐藏的工具列表（用户无法禁用的系统保留工具）
#
# 新配置方式：
# - disabled_tools: 存储在 global_settings 表中，由 GlobalToolSettingsService 管理
# - protected_tools: 存储在 global_settings 表中，由 GlobalToolSettingsService 管理

# --- Ollama Embedding 配置 ---
# 用于本地 embedding 服务的配置
# 在 Docker 环境中强制使用服务名称 ollama:11434，忽略环境变量中的 localhost
RUNNING_IN_DOCKER = os.getenv("RUNNING_IN_DOCKER", "False").lower() == "true"
if RUNNING_IN_DOCKER:
    OLLAMA_CONFIG = {
        "BASE_URL": "http://ollama:11434",
        "MODEL": "bge-m3",
    }
    QWEN_EMBEDDING_CONFIG = {
        "BASE_URL": "http://ollama:11434",
        "MODEL": "qwen3-embedding:0.6b",
    }
else:
    OLLAMA_CONFIG = {
        "BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "MODEL": os.getenv("OLLAMA_MODEL", "bge-m3"),
    }
    QWEN_EMBEDDING_CONFIG = {
        "BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "MODEL": os.getenv("QWEN_EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
    }

# --- Ollama Vision 配置 ---
# 用于本地视觉模型（图片转文字），支持多模态的模型
if RUNNING_IN_DOCKER:
    OLLAMA_VISION_CONFIG = {
        "BASE_URL": "http://ollama:11434",
        "MODEL": os.getenv("OLLAMA_VISION_MODEL", "qwen3.5:0.8b"),
    }
else:
    OLLAMA_VISION_CONFIG = {
        "BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "MODEL": os.getenv("OLLAMA_VISION_MODEL", "qwen3.5:0.8b"),
    }

# --- [DISABLED] 印象总结功能（flash模型）已禁用 ---
# SUMMARY_MODEL = "gemini-2.5-flash-custom"

# --- 塔罗牌占卜功能配置 ---
TAROT_CONFIG = {
    "CARDS_PATH": "src/chat/features/tarot/cards/",  # 存放78张塔罗牌图片的目录路径
    "CARD_FILE_EXTENSION": ".jpg",  # 图片文件的扩展名
}

# --- 两阶段回复管线：Stage 1（工具路由模型）的系统提示 ---
# Stage 1 的唯一职责：判断是否需要调用工具并执行，不写最终回复
TOOL_ROUTER_SYSTEM_PROMPT = (
    "你是一个工具调用路由助手，不是聊天机器人。"
    "判断用户消息是否需要调用工具：需要就调用正确的工具，不需要就返回空回复。\n"
    "\n"
    "【核心原则：是否存在信息缺口】\n"
    "- 关键判断是「你是否已经掌握回答所需的信息」。"
    "只要存在信息缺口——用户提到你不了解的名词、角色、设定、社区成员，"
    "或询问需要查证的事实——就必须调用对应工具查证，不要凭猜测回答。这一点对闲聊同样适用。\n"
    "- 只有纯打招呼、情绪表达、寒暄，且确实不存在任何信息缺口时，才返回空回复。\n"
    "- 各工具的具体适用场景，以本次注入的工具描述为准。\n"
    "\n"
    "【警告类工具特别约束】\n"
    "「警告用户」工具会令用户被临时封禁，代价极高，必须严格依照其触发条件使用：\n"
    "- 仅当用户明确、恶意、多次违反时才可调用；\n"
    "- 单次无心之言、玩笑、亲昵称呼（如「宝宝」「小类」）绝不构成违规；\n"
    "- 任何模棱两可或证据不足的情况，一律不调用，返回空回复；\n"
    "- 严禁滥用：宁可放过，不可误封。\n"
    "严格要求：不要扮演任何角色，不要与用户闲聊，不要复述或解释工具结果，不要写最终回复。\n"
    "你的输出不会被用户看到，只会被用于决定后续流程。"
)

# Stage 2（写作模型）收到的工具调用记录块标题
TOOL_RESULTS_BLOCK_HEADER = "【工具查询记录】"
TOOL_RESULTS_BLOCK_INSTRUCTION = (
    "以上是前置工具调用阶段已经获取到的数据。请基于这些数据（若存在）以及你的角色设定，"
    "撰写最终回复。如果上面没有任何工具记录，说明本次无需调用工具，请直接回复用户。"
)

# --- RAG (Retrieval-Augmented Generation) 配置 ---
# RAG 搜索返回的结果数量
RAG_N_RESULTS_DEFAULT = 8  # 普通聊天的默认值
FORUM_SEARCH_DEFAULT_LIMIT = 5  # 论坛搜索工具返回结果的默认数量

# --- 联网搜索配置 ---
RUNNING_IN_DOCKER = os.getenv("RUNNING_IN_DOCKER", "False").lower() == "true"
_searxng_url = (
    os.getenv("SEARXNG_URL", "http://searxng:8080")
    if RUNNING_IN_DOCKER
    else os.getenv("SEARXNG_URL", "http://localhost:8888")
)
WEB_SEARCH_CONFIG = {
    "SEARXNG_URL": _searxng_url,
    "MAX_RESULTS": int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5")),
    "TIMEOUT": float(os.getenv("WEB_SEARCH_TIMEOUT", "10")),
    "SCRAPE_TIMEOUT": float(os.getenv("WEB_SCRAPE_TIMEOUT", "15")),
    "SCRAPE_MAX_LENGTH": int(os.getenv("WEB_SCRAPE_MAX_LENGTH", "5000")),
    "RATE_LIMIT_SEARCH": int(os.getenv("WEB_SEARCH_RATE_LIMIT_SEARCH", "3")),
    "RATE_LIMIT_SCRAPE": int(os.getenv("WEB_SEARCH_RATE_LIMIT_SCRAPE", "5")),
    "RATE_LIMIT_WINDOW": int(os.getenv("WEB_SEARCH_RATE_LIMIT_WINDOW", "60")),
}

# RAG 搜索结果的距离阈值。分数越低越相似。
# 只有距离小于或等于此值的知识才会被采纳。
# 注意：bge-m3 模型使用余弦距离，范围是 [0, 2]
# 0 表示完全匹配，1 表示完全相反，2 表示最不相关
RAG_MAX_DISTANCE = 0.5  # bge-m3 模型的推荐值（教程搜索）
FORUM_RAG_MAX_DISTANCE = 0.65  # bge-m3 模型的推荐值（论坛搜索）- 放宽以支持语义相似匹配

# --- 教程 RAG 配置 ---
TUTORIAL_RAG_CONFIG = {
    "TOP_K_VECTOR": 20,  # 向量搜索返回的初始结果数量
    "TOP_K_FTS": 20,  # 全文搜索返回的初始结果数量
    "HYBRID_SEARCH_FINAL_K": 5,  # 混合搜索后最终选择的文本块数量
    "RRF_K": 60,  # RRF 算法中的排名常数
    "MAX_PARENT_DOCS": 3,  # 最终返回给AI的父文档最大数量
}

# --- 工具专属配置 ---
# 调用教程搜索工具后，在回复末尾追加的后缀
TUTORIAL_SEARCH_SUFFIX = "\n\n> 虽然我努力学习了，但教程的内容可能不是最新的哦！如果我的回答没解决你的问题，欢迎去答疑区问问大佬们！"

# --- 社区设定 RAG 配置 ---
COMMUNITY_SETTINGS_RAG_CONFIG = {
    "TOP_K_VECTOR": 20,
    "TOP_K_FTS": 20,
    "HYBRID_SEARCH_FINAL_K": 5,  # 社区设定返回最多5条chunks
    "RRF_K": 60,
    "MAX_PARENT_DOCS": 5,  # 社区设定返回更多父文档
}

# --- Forum 搜索 RAG 配置 ---
FORUM_RAG_CONFIG = {
    "TOP_K_VECTOR": 20,  # 向量搜索返回的初始结果数量
    "TOP_K_FTS": 20,  # 全文搜索返回的初始结果数量
    "HYBRID_SEARCH_FINAL_K": 5,  # 混合搜索后最终选择的帖子数量
    "RRF_K": 60,  # RRF 算法中的排名常数
    "EXACT_MATCH_BOOST": 1000.0,  # 精确匹配（content包含完整query）的额外加分数值
}

# --- 消息设置 ---
MESSAGE_SETTINGS = {
    "DM_THRESHOLD": 300,  # 当消息长度超过此值时，通过私信发送
}

GEMINI_TEXT_GEN_CONFIG = {
    "temperature": 0.1,
    "max_output_tokens": 200,
}

GEMINI_VISION_GEN_CONFIG = {
    "temperature": 1.1,
    "max_output_tokens": 3000,
}

# --- [DISABLED] 印象总结功能（flash模型）已禁用 ---
# GEMINI_SUMMARY_GEN_CONFIG = {
#     "temperature": 0.3,
#     "max_output_tokens": 8000,
# }

COOLDOWN_RATES = {
    "default": 2,  # 每分钟请求次数
    "coffee": 5,  # 每分钟请求次数
}
# (min, max) 分钟
BLACKLIST_BAN_DURATION_MINUTES = (15, 30)

# --- API 并发与密钥配置 ---
MAX_CONCURRENT_REQUESTS = 50  # 同时处理的最大API请求数

# --- API 密钥重试与轮换配置 ---
API_RETRY_CONFIG = {
    "MAX_ATTEMPTS_PER_KEY": 1,  # 单个密钥在因可重试错误而被轮换前，允许的最大尝试次数
    "RETRY_DELAY_SECONDS": 1,  # 对同一个密钥进行重试前的延迟（秒）
    "EMPTY_RESPONSE_MAX_ATTEMPTS": 2,  # 当API返回空回复（可能因安全设置）时，使用同一个密钥进行重试的最大次数
}

# --- Provider 故障转移重试配置 ---
# 当 Provider 请求失败时，在执行故障转移之前先重试的配置
PROVIDER_RETRY_CONFIG = {
    "MAX_RETRIES": 10,  # 故障转移前对同一 Provider 的最大重试次数（不含首次请求）
    "RETRY_DELAY_SECONDS": 1,  # 每次重试之间的延迟（秒）
}

# 定义不同安全风险等级对应的信誉惩罚值
SAFETY_PENALTY_MAP = {
    "NEGLIGIBLE": 0,  # 可忽略
    "LOW": 5,  # 低风险
    "MEDIUM": 15,  # 中等风险
    "HIGH": 30,  # 高风险
}

FORUM_SYNC_DELAY_SECONDS = 3600
# --- 个人记忆功能 ---
PERSONAL_MEMORY_CONFIG = {
    "summary_threshold": 20,  # 触发总结的消息数量阈值 (测试用 5, 原为 50)
}

# --- 对话记忆功能 (永久记忆 RAG) ---
CONVERSATION_MEMORY_CONFIG = {
    "enabled": True,  # 是否启用对话记忆功能
    "block_size": 10,  # 每个对话块包含的消息数量
    "retrieval_top_k": 1,  # 检索返回的对话块数量
    "max_blocks_per_user": 100,  # 每个用户最多保留的对话块数量
    "show_time_marker": True,  # 是否在对话块前显示时间标记
    # 混合搜索配置 (参考论坛搜索)
    "top_k_vector": 10,  # 向量搜索返回数量
    "top_k_fts": 10,  # BM25搜索返回数量
    "rrf_k": 60,  # RRF 融合常数
    "max_vector_distance": 0.65,  # 向量距离阈值 (余弦距离)
    # --- [DISABLED] 印象总结功能（flash模型）已禁用 ---
    # "summary_trigger_blocks": 2,
}

# --- 频道记忆功能 ---
CHANNEL_MEMORY_CONFIG = {
    "raw_history_limit": 35,  # 从Discord API获取的原始消息数量
    "formatted_history_limit": 35,  # 格式化为AI模型可用的对话历史消息数量
}

# --- 论坛帖子轮询配置 ---
# 在这里添加需要轮询的论坛频道ID
FORUM_SEARCH_CHANNEL_IDS = _parse_ids("FORUM_SEARCH_CHANNEL_IDS")

# 每日轮询任务处理的帖子数量上限
FORUM_POLL_THREAD_LIMIT = 100

# 轮询任务的并发数
FORUM_POLL_CONCURRENCY = 20

# --- 论坛帖子清理配置 ---
# 是否启用失效帖子清理（可用于调试或临时禁用）
FORUM_CLEANUP_ENABLED = os.getenv("FORUM_CLEANUP_ENABLED", "true").lower() == "true"

# --- 论坛帖子 ChromeDB 迁移配置 ---
# --- 社区设定审核配置 ---
COMMUNITY_SETTINGS_CONFIG = {
    # 审核系统设置
    "review_settings": {
        # 审核的持续时间（分钟）
        "review_duration_minutes": 5,
        # 审核时间结束后，通过所需的最低赞成票数
        "approval_threshold": 3,
        # 在审核期间，可立即通过的赞成票数
        "instant_approval_threshold": 10,
        # 在审核期间，可立即否决的反对票数
        "rejection_threshold": 5,
        # 投票使用的表情符号
        "vote_emoji": "✅",
        "reject_emoji": "❌",
    },
}

# --- 频道禁言功能 ---
CHANNEL_MUTE_CONFIG = {
    "VOTE_THRESHOLD": 5,  # 禁言投票通过所需的票数 (方便测试设为2)
    "VOTE_DURATION_MINUTES": 3,  # 投票的有效持续时间（分钟）
    "MUTE_DURATION_MINUTES": 30,  # 禁言的持续时间（分钟）
}

# --- 图片处理配置 ---
IMAGE_PROCESSING_CONFIG = {
    "SEQUENTIAL_PROCESSING": True,  # 顺序处理所有图片（一张一张处理，防止内存溢出）
    "MAX_IMAGES_PER_MESSAGE": 9,  # 单次消息最多处理的图片数量（Discord限制为9张）
}

# --- 调试配置 ---
DEBUG_CONFIG = {
    "LOG_FINAL_CONTEXT": False,  # 是否在日志中打印发送给AI的最终上下文，用于调试
    "LOG_AI_FULL_CONTEXT": os.getenv("LOG_AI_FULL_CONTEXT", "False").lower()
    == "true",
}

# --- 文爱过滤配置 ---
CONTENT_FILTER_BASE_KEYWORDS = [
    # 直白性行为
    "做爱", "性交", "操逼", "草逼", "插入", "口交", "肛交", "手交", "足交", "乳交",
    "自慰", "手淫", "打飞机", "高潮", "射精", "颜射", "内射", "中出", "潮吹",
    # 性器官
    "阴茎", "鸡巴", "大屌", "龟头", "肉棒", "阴道", "小穴", "肉穴", "阴蒂", "阴唇",
    "奶子", "乳头", "奶头",
    # 超出亲亲抱抱的亲密动作
    "舌吻", "湿吻", "摸胸", "揉胸", "揉奶", "吸奶", "含屌", "骑乘", "后入", "抽插",
    "活塞", "床叫", "舔逼", "舔下面",
    # 性暗示/场景
    "啪啪啪", "交配", "发情", "发骚", "淫荡", "捆绑", "露出", "乱伦", "强奸",
    "迷奸", "春药", "媚药", "催情", "做牛",
    # 身体部位隐晦
    "双峰", "酥胸", "玉乳", "欧派", "白兔", "胸脯", "罩杯",
    "私处", "秘密花园", "小妹妹", "那话儿", "分身", "巨物", "硬物", "坚挺", "火热", "帐篷",
    "翘臀", "蜜桃臀",
    # 间接性行为
    "顶进去", "塞进", "深入", "贯穿", "冲刺", "撞击",
    "湿了", "流水", "白浊", "黏糊糊", "滑腻",
    "呻吟", "娇喘", "喘息", "闷哼", "颤抖", "痉挛", "紧绷", "酥麻", "快感", "敏感点",
    # 擦边球暗示
    "调教", "服从", "支配", "臣服",
    "情动", "意乱情迷",
    "不行了", "受不了",
    "推倒", "扑倒", "压在身下", "脱衣服", "解扣子",
    "开房", "过夜", "床上", "浴缸", "一起洗", "共浴",
    "磨蹭", "捅",
    # 英文
    "fuck", "sex", "cock", "dick", "pussy", "clit", "tits", "boobs",
    "horny", "penetrate", "blowjob", "handjob", "cum", "orgasm",
    "masturbate", "slut", "naked", "nude", "bdsm", "hentai",
    "moan", "groan", "thrust", "grind", "strip", "undress",
    "foreplay", "kinky", "aroused", "excited",
    "dominate", "submissive", "master", "slave", "breed", "mating",
    "boner", "erection", "hard", "stiff", "wet", "moist", "dripping",
    "make love", "hook up", "get laid", "making out",
    "nipple", "penis", "vagina", "clitoris", "breast",
]
