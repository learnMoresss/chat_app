"""上下文预算：局部上限 + 总软上限；估算计量并标记。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


def approx_tokens(text: str) -> int:
    t = text or ""
    cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    other = max(0, len(t) - cjk)
    return max(1, int(cjk / 1.5 + other / 4) + 1) if t else 0


@dataclass
class BudgetConfig:
    total_input_soft: int = 16384
    output_reserve: int = 4096
    tool_result_reserve: int = 4096
    safety_margin: int = 1024
    persona_state_max: int = 1500
    work_summary_max: int = 1200
    recent_turns_max: int = 6
    recent_tokens_max: int = 6000
    memory_items_max: int = 8
    memory_tokens_max: int = 1200
    hot_preload_items: int = 3
    hot_preload_tokens: int = 400
    query_recall_items: int = 5
    query_recall_tokens: int = 800
    single_memory_max: int = 120


@dataclass
class BudgetReport:
    ok: bool
    estimated: bool = True
    parts: Dict[str, int] = field(default_factory=dict)
    total: int = 0
    limit: int = 0
    trimmed: List[str] = field(default_factory=list)
    reason: str = ""


class ContextBudgeter:
    def __init__(self, config: BudgetConfig | None = None) -> None:
        self.config = config or BudgetConfig()

    def available_input(self) -> int:
        c = self.config
        return max(
            512,
            min(
                c.total_input_soft,
                # 产品软上限；真实模型窗需配置覆盖
                c.total_input_soft,
            ),
        )

    def pack_memories(
        self,
        hot: List[Dict[str, Any]],
        recalled: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], BudgetReport]:
        """热预载 + 查询召回合并去重，受条数与 token 双限。"""
        c = self.config
        seen = set()
        picked: List[Dict[str, Any]] = []
        tokens = 0
        trimmed: List[str] = []

        def take(items: List[Dict[str, Any]], max_n: int, max_tok: int, label: str) -> None:
            nonlocal tokens
            n = 0
            part_tok = 0
            for it in items:
                mid = it.get("id") or it.get("text")
                if not mid or mid in seen:
                    continue
                body = (it.get("text") or "")[: max(20, c.single_memory_max * 2)]
                t = approx_tokens(body)
                if t > c.single_memory_max:
                    body = body[: c.single_memory_max * 2]
                    t = approx_tokens(body)
                    it = {**it, "text": body}
                if n >= max_n or part_tok + t > max_tok or tokens + t > c.memory_tokens_max:
                    trimmed.append(f"{label}:{mid}")
                    continue
                if len(picked) >= c.memory_items_max:
                    trimmed.append(f"cap:{mid}")
                    continue
                seen.add(mid)
                picked.append(it)
                n += 1
                part_tok += t
                tokens += t

        take(hot[: c.hot_preload_items * 2], c.hot_preload_items, c.hot_preload_tokens, "hot")
        take(recalled, c.query_recall_items, c.query_recall_tokens, "recall")

        report = BudgetReport(
            ok=tokens <= c.memory_tokens_max and len(picked) <= c.memory_items_max,
            estimated=True,
            parts={"memory": tokens, "memory_items": len(picked)},
            total=tokens,
            limit=c.memory_tokens_max,
            trimmed=trimmed,
        )
        return picked, report

    def check_final(
        self,
        *,
        system: str = "",
        instructions: str = "",
        recent: str = "",
        user_text: str = "",
        tools_schema: str = "",
    ) -> BudgetReport:
        parts = {
            "system": approx_tokens(system),
            "instructions": approx_tokens(instructions),
            "recent": approx_tokens(recent),
            "user": approx_tokens(user_text),
            "tools": approx_tokens(tools_schema),
            "output_reserve": self.config.output_reserve,
            "tool_reserve": self.config.tool_result_reserve,
            "safety": self.config.safety_margin,
        }
        total = sum(parts.values())
        limit = self.available_input() + self.config.output_reserve + self.config.tool_result_reserve
        # 产品软上限看「输入侧」
        input_total = parts["system"] + parts["instructions"] + parts["recent"] + parts["user"] + parts["tools"]
        ok = input_total <= self.config.total_input_soft
        return BudgetReport(
            ok=ok,
            estimated=True,
            parts=parts,
            total=input_total,
            limit=self.config.total_input_soft,
            reason="" if ok else "input_over_soft_limit",
        )
