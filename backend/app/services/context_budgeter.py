from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TIKTOKEN_AVAILABLE = False
try:
    import tiktoken as _tiktoken_mod  # type: ignore[import-untyped]
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _tiktoken_mod = None  # type: ignore[assignment]


@lru_cache(maxsize=1)
def _get_encoding():
    if not _TIKTOKEN_AVAILABLE or _tiktoken_mod is None:
        return None
    return _tiktoken_mod.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    enc = _get_encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Conservative fallback: ~1.5 tokens/char for CJK-heavy text
    return max(1, int(len(text) * 1.5))


@dataclass
class SectionBudget:
    name: str
    content: str
    hard_limit_tokens: int  # 0 = no compression
    priority: int           # 1 = must keep, higher = drop first
    shrinker: Optional[Callable[[str, int], str]] = None


def _truncate_shrinker(content: str, target: int) -> str:
    enc = _get_encoding()
    if enc is None:
        # Character-based fallback
        approx_chars = target * 3 // 2
        return content[:approx_chars] + "\n…[截断]" if len(content) > approx_chars else content
    tokens = enc.encode(content)
    if len(tokens) <= target:
        return content
    return enc.decode(tokens[:target]) + "\n…[截断]"


def _json_blueprint_shrinker(content: str, target: int) -> str:
    current_best = content
    try:
        bp = json.loads(content)
        for ch in bp.get("characters", []):
            ch.pop("backstory", None)
            ch.pop("abilities", None)
        for loc in bp.get("world_setting", {}).get("key_locations", []):
            loc["description"] = (loc.get("description") or "")[:80]
        current_best = json.dumps(bp, ensure_ascii=False)
        if count_tokens(current_best) <= target:
            return current_best
        # Further: drop factions details
        for faction in bp.get("world_setting", {}).get("factions", []):
            faction.pop("description", None)
        current_best = json.dumps(bp, ensure_ascii=False)
        if count_tokens(current_best) <= target:
            return current_best
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return _truncate_shrinker(current_best, target)


class ContextBudgeter:
    """Token-budget enforcer for prompt section lists.

    Sections with higher priority numbers are dropped first when the total
    budget is exceeded. Sections with priority <= 2 are never dropped, only
    compressed (if a shrinker is provided).
    """

    # Section name → (hard_limit_tokens, priority)
    DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
        "[章节导演脚本](JSON)":               (1500, 1),
        "[当前章节目标]":                      (400,  1),
        "[篇幅与排版要求]":                   (300,  1),
        "[禁止角色](本章不允许提及)":          (300,  1),
        "[小说宪法](必须遵守)":               (1200, 1),
        "[世界蓝图](JSON，已裁剪)":            (4000, 2),
        "[Writer 人格](写作风格指导)":         (800,  2),
        "[上一章摘要]":                       (800,  3),
        "[上一章结尾]":                       (1200, 3),
        "[项目长期记忆](摘要/剧情线)":        (1500, 4),
        "[记忆层上下文]":                     (2000, 4),
        "[伏笔提醒](本章需要发展的伏笔)":     (1000, 4),
        "[RAG精筛上下文](含POV裁剪)":         (2000, 5),
        "[检索到的章节摘要](Markdown)":       (1000, 5),
        "[势力关系](参考信息)":               (800,  5),
        "[检索到的剧情上下文](Markdown)":     (3000, 6),
    }

    def __init__(self, total_budget_tokens: int = 16000):
        self.total_budget = total_budget_tokens

    def fit(
        self,
        sections: List[Tuple[str, str]],
    ) -> List[Tuple[str, str]]:
        budgeted: List[SectionBudget] = []
        for name, content in sections:
            limit, pri = self.DEFAULT_LIMITS.get(name, (0, 7))
            shrinker = _json_blueprint_shrinker if "蓝图" in name else _truncate_shrinker
            budgeted.append(SectionBudget(
                name=name,
                content=content,
                hard_limit_tokens=limit,
                priority=pri,
                shrinker=shrinker,
            ))

        def _shrink(s: SectionBudget, text: str, target: int) -> str:
            fn = s.shrinker or _truncate_shrinker
            return fn(text, target)

        # Pass 1: compress each section to its hard limit
        compressed: List[Tuple[SectionBudget, str]] = []
        for s in budgeted:
            text = s.content
            if s.hard_limit_tokens and count_tokens(text) > s.hard_limit_tokens:
                text = _shrink(s, text, s.hard_limit_tokens)
            compressed.append((s, text))

        # Pass 2: drop/shrink by priority until within budget
        while _total_tokens(compressed) > self.total_budget and compressed:
            max_pri = max(s.priority for s, _ in compressed)
            if max_pri <= 2:
                logger.warning(
                    "ContextBudgeter: cannot shed more sections (all priority<=2); "
                    "total=%d budget=%d",
                    _total_tokens(compressed),
                    self.total_budget,
                )
                break
            victim_idx = next(
                i for i, (s, _) in enumerate(compressed) if s.priority == max_pri
            )
            s, c = compressed[victim_idx]
            if s.priority >= 5:
                compressed.pop(victim_idx)
                logger.debug("ContextBudgeter: dropped section '%s'", s.name)
            else:
                new_target = max(200, count_tokens(c) // 2)
                new_c = _shrink(s, c, new_target)
                if count_tokens(new_c) >= count_tokens(c):
                    compressed.pop(victim_idx)
                else:
                    compressed[victim_idx] = (s, new_c)

        return [(s.name, c) for s, c in compressed]


def _total_tokens(pairs: List[Tuple[SectionBudget, str]]) -> int:
    return sum(count_tokens(c) for _, c in pairs)
