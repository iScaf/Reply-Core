# -*- coding: utf-8 -*-
"""一次性清理脚本：chat_config.py 删除已删功能的配置块（用后即删）"""
import re

p = "src/chat/config/chat_config.py"
lines = open(p, encoding="utf-8").readlines()

DEAD = [
    "COMFYUI_CONFIG", "GPT_IMAGE_CONFIG", "THREAD_PRAISE_MODEL", "FEEDING_MODEL",
    "CONFESSION_MODEL", "RAG_N_RESULTS_THREAD_COMMENTOR", "GEMINI_GIFT_GEN_CONFIG",
    "GEMINI_THREAD_PRAISE_CONFIG", "GEMINI_CONFESSION_GEN_CONFIG",
    "GEMINI_FEEDING_GEN_CONFIG", "GEMINI_THREAD_COMMENTOR_GEN_CONFIG",
    "COIN_REWARD_FORUM_CHANNEL_IDS", "COIN_REWARD_GUILD_IDS",
    "COIN_REWARD_DELAY_SECONDS", "THREAD_COMMENTOR_CONFIG", "AFFECTION_CONFIG",
    "FEEDING_CONFIG", "CONFESSION_CONFIG", "COIN_CONFIG", "PROMPT_CONFIG",
    "GIFT_SYSTEM_PROMPT", "GIFT_PROMPT", "CONFESSION_PERSONA_INJECTION",
    "CONFESSION_PROMPT", "WARMUP_MESSAGES", "FORUM_VECTOR_DB_PATH",
    "FORUM_VECTOR_DB_COLLECTION_NAME",
]

# 找每个待删常量的起始行（顶层 "NAME = " 或 "NAME: "），删除到下一个顶层语句行（非缩进、非空、非续行）
top_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:=]")
out = []
i = 0
n = len(lines)
while i < n:
    line = lines[i]
    m = top_re.match(line)
    if m and m.group(1) in DEAD:
        # 向前回溯：删除紧邻的注释行（# --- xxx --- 之类），但保留至少与上一个块的空行
        while out and (out[-1].strip().startswith("#") and not out[-1].startswith("# --- 论坛")):
            # 只回吞与该常量直接相关的注释（连续 # 行）
            if out[-1].strip().startswith("# ---") or out[-1].strip().startswith("#"):
                out.pop()
            else:
                break
        # 找块结束：下一个顶层语句
        j = i + 1
        while j < n:
            if top_re.match(lines[j]) or (lines[j].startswith("# --- ")):
                break
            j += 1
        # 也吞掉下一个 "# --- xxx ---" 注释头（它描述的块已删或保留，保留更安全：不吞）
        i = j
        continue
    out.append(line)
    i += 1

s = "".join(out)

# 更名
s = s.replace(
    """# --- 世界之书 RAG 配置 ---
WORLD_BOOK_RAG_CONFIG = {
    "TOP_K_VECTOR": 20,
    "TOP_K_FTS": 20,
    "HYBRID_SEARCH_FINAL_K": 5,  # 世界之书返回最多5条chunks
    "RRF_K": 60,
    "MAX_PARENT_DOCS": 5,  # 世界之书返回更多父文档
}""",
    """# --- 社区设定 RAG 配置 ---
COMMUNITY_SETTINGS_RAG_CONFIG = {
    "TOP_K_VECTOR": 20,
    "TOP_K_FTS": 20,
    "HYBRID_SEARCH_FINAL_K": 5,  # 社区设定返回最多5条chunks
    "RRF_K": 60,
    "MAX_PARENT_DOCS": 5,  # 社区设定返回更多父文档
}""",
)

# WORLD_BOOK_CONFIG 更名并只保留 review_settings
s = s.replace(
    """# --- 世界之书向量化任务配置 ---
WORLD_BOOK_CONFIG = {
    "VECTOR_INDEX_UPDATE_INTERVAL_HOURS": 6,  # 向量索引更新间隔（小时）
    # 审核系统设置
    "review_settings": {""",
    """# --- 社区设定审核配置 ---
COMMUNITY_SETTINGS_CONFIG = {
    # 审核系统设置
    "review_settings": {""",
)
# 删除 personal_profile_review_settings 与 work_event_review_settings 两个子块
s = re.sub(
    r'    # 个人资料审核设置\n    "personal_profile_review_settings": \{.*?\},\n',
    "",
    s,
    flags=re.DOTALL,
)
s = re.sub(
    r'    # 自定义工作/卖屁股事件审核设置\n    "work_event_review_settings": \{.*?\},\n',
    "",
    s,
    flags=re.DOTALL,
)

open(p, "w", encoding="utf-8", newline="").write(s)
print("cleaned")
for name in DEAD + ["WORLD_BOOK"]:
    if name in s:
        print("残留:", name)
