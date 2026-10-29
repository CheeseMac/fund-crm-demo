"""离线规则实现：不联网、不依赖模型，覆盖有限中文句式。"""
from __future__ import annotations

from datetime import date
from typing import Sequence

from app.ai import text_rules
from app.ai.entity_match import choose_target
from app.ai.ports import ExtractionResult


class RuleIntentExtractor:
    model_id = "offline_rules"

    def extract(self, message: str, customer_names: Sequence[str],
                contact_pairs: Sequence[tuple[str, str]],
                today: date | None = None) -> ExtractionResult:
        customer_name, contact_name = choose_target(
            message, list(customer_names), list(contact_pairs))
        drafts, warnings = text_rules.build_drafts(
            message, customer_name, contact_name, today)
        return ExtractionResult(drafts=drafts, warnings=warnings,
                                model_id=self.model_id)
