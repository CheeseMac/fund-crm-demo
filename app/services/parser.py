"""兼容门面：自然语言解析已拆分至 app/ai（text_rules / entity_match / adapters）。

旧公共函数名保留，供 services.assistant 与既有调用方使用；
新代码请直接依赖 app/ai/ports.IntentExtractor。
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

from sqlalchemy.orm import Session

from app.ai.text_rules import (  # noqa: F401  (re-export)
    TAG_ALIASES, local_today, extract_tags, parse_date,
    is_due_query, due_query_end,
)
from app.ai.entity_match import (  # noqa: F401
    get_visible_targets, recognize_target, resolve_actions,
)
from app.ai.adapters.rule_adapter import RuleIntentExtractor
from app.ai.adapters.llm_adapter import LLMIntentExtractor
from app.dependencies import Actor
from app.models import Customer, Contact
from app.schemas import ActionDraft, ResolvedAction


def _name_context(customers: list[Customer], contacts: list[Contact]):
    customer_by_id = {c.id: c for c in customers}
    names = [c.name for c in customers]
    pairs = [(c.name, customer_by_id[c.customer_id].name)
             for c in contacts if c.customer_id in customer_by_id]
    return names, pairs


def fallback_parse(message: str, customers: list[Customer],
                   contacts: list[Contact], today: date | None = None):
    names, pairs = _name_context(customers, contacts)
    result = RuleIntentExtractor().extract(message, names, pairs, today)
    return result.drafts, result.warnings


def llm_parse(message: str, customers: list[Customer], contacts: list[Contact],
              today: date | None = None) -> list[ActionDraft]:
    names, pairs = _name_context(customers, contacts)
    result = LLMIntentExtractor().extract(message, names, pairs, today)
    return result.drafts
