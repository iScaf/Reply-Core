# -*- coding: utf-8 -*-
"""Skill 文件服务：skills/ 目录的扫描、读写与提示词注入。

Skill = Claude Code 风格的 Markdown 文档（skills/<name>/SKILL.md），
frontmatter 携带元数据（display_name/injection_mode/enabled），
正文为注入 AI 提示词的知识内容（速查表/查询模板/规则）。

安全边界：写入仅允许 skills/<name>/SKILL.md，name 强校验防路径穿越。
"""
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

log = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"  # 项目根/skills
SKILL_FILE = "SKILL.md"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class SkillService:
    def __init__(self) -> None:
        self._cache: Optional[List[Dict[str, Any]]] = None

    # ---------- 校验 ----------

    @staticmethod
    def _validate_name(name: str) -> str:
        """校验技能名，防路径穿越；非法抛 ValueError。"""
        name = (name or "").strip().lower()
        if not NAME_PATTERN.match(name):
            raise ValueError(
                "技能名仅允许小写字母、数字与连字符（1-49 字符，字母开头）"
            )
        return name

    @staticmethod
    def _skill_path(name: str) -> Path:
        return SKILLS_DIR / name / SKILL_FILE

    # ---------- 读取 ----------

    @staticmethod
    def _parse_skill_file(path: Path) -> Optional[Dict[str, Any]]:
        """解析单个 SKILL.md：frontmatter 元数据 + 正文。文件异常返回 None。"""
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning(f"[Skill] 读取 {path} 失败: {e}")
            return None
        meta: Dict[str, Any] = {}
        match = _FRONTMATTER_RE.match(raw)
        if match:
            try:
                meta = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError as e:
                log.warning(f"[Skill] {path} frontmatter 解析失败: {e}")
            content = raw[match.end():]
        else:
            content = raw
        if not isinstance(meta, dict):
            meta = {}
        return {
            "name": path.parent.name,
            "display_name": meta.get("display_name") or path.parent.name,
            "description": meta.get("description") or "",
            "injection_mode": meta.get("injection_mode") or "prompt",
            "enabled": bool(meta.get("enabled", True)),
            "content": content.strip(),
            "updated_at": path.stat().st_mtime,
        }

    def list_skills(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """扫描 skills/ 目录，返回全部技能（内存缓存）。"""
        if self._cache is not None and not force_refresh:
            return self._cache
        skills: List[Dict[str, Any]] = []
        if SKILLS_DIR.is_dir():
            for skill_dir in sorted(SKILLS_DIR.iterdir()):
                if not skill_dir.is_dir():
                    continue
                parsed = self._parse_skill_file(skill_dir / SKILL_FILE)
                if parsed:
                    skills.append(parsed)
        self._cache = skills
        log.info(f"[Skill] 已加载 {len(skills)} 个技能: {[s['name'] for s in skills]}")
        return skills

    def invalidate(self) -> None:
        """保存/外部修改后清空缓存，下次读取重新扫描。"""
        self._cache = None

    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        name = self._validate_name(name)
        for skill in self.list_skills():
            if skill["name"] == name:
                return skill
        return None

    def get_prompt_skills(self) -> List[Dict[str, Any]]:
        """返回启用的 prompt 注入型技能（拼提示词用）。"""
        return [
            s
            for s in self.list_skills()
            if s["enabled"] and s["injection_mode"] == "prompt"
        ]

    def build_prompt_block(self) -> Optional[str]:
        """把全部启用技能拼成一个提示词块；无技能返回 None。"""
        skills = self.get_prompt_skills()
        if not skills:
            return None
        parts = [
            f'<skill name="{s["name"]}">\n{s["content"]}\n</skill>' for s in skills
        ]
        return "\n\n".join(parts)

    # ---------- 写入 ----------

    def save_skill(
        self,
        name: str,
        content: str,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        injection_mode: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """创建/更新技能：写入 skills/<name>/SKILL.md（保留并合并 frontmatter）。"""
        name = self._validate_name(name)
        if injection_mode and injection_mode not in ("prompt", "on_demand"):
            raise ValueError("injection_mode 仅允许 prompt / on_demand")

        existing = self.get_skill(name)
        meta = {
            "name": name,
            "display_name": display_name
            or (existing["display_name"] if existing else name),
            "description": description
            if description is not None
            else (existing["description"] if existing else ""),
            "injection_mode": injection_mode
            or (existing["injection_mode"] if existing else "prompt"),
            "enabled": enabled
            if enabled is not None
            else (existing["enabled"] if existing else True),
        }

        frontmatter = yaml.safe_dump(
            meta, allow_unicode=True, sort_keys=False
        ).strip()
        text = f"---\n{frontmatter}\n---\n\n{content.strip()}\n"

        path = self._skill_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)  # mkdir 仅在 skills/ 内，name 已校验
        path.write_text(text, encoding="utf-8")
        self.invalidate()
        log.info(f"[Skill] 技能已保存: {name}（{len(content)} 字符）")
        return self.get_skill(name)  # type: ignore[return-value]


# 模块级单例
skill_service = SkillService()
