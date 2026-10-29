"""AI 提供者端口：业务层只依赖这些接口，具体实现（规则/LLM/ASR）可替换。

与 docs/blueprints/contracts.py 对齐，但以内置 ActionDraft 作为草稿契约，
保证 V1 路由与测试完全兼容。端口实现不得持有数据库写权限。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, Sequence

from app.schemas import ActionDraft


@dataclass
class ExtractionResult:
    drafts: list[ActionDraft] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model_id: str | None = None


class IntentExtractor(Protocol):
    """一句话 -> 候选操作草稿。

    只传入当前销售有权访问的最小必要上下文（客户名与联系人名），
    不传持仓金额、历史纪要或整租户数据。
    """

    def extract(self, message: str, customer_names: Sequence[str],
                contact_pairs: Sequence[tuple[str, str]],
                today: date | None = None) -> ExtractionResult: ...
