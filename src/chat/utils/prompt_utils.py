import random
from src.chat.config.emoji_config import EMOJI_MAPPINGS


def replace_emojis(text: str) -> str:
    """
    根据 emoji_config.py 中的映射规则，
    将文本中的自定义表情占位符（如 <微笑>）替换为对应的 Discord 自定义表情（如 <:xianhua:12345>）。
    """
    processed_text = text
    for pattern, replacement_list in EMOJI_MAPPINGS:
        if replacement_list:
            processed_text = pattern.sub(
                lambda m: random.choice(replacement_list), processed_text
            )
    return processed_text
