"""纯文本规则：中文有限句式 -> 候选操作草稿。无数据库、无网络、无框架依赖。

设计约束（docs/05 §6）：规则层只产出候选事实（ActionDraft），
永远不直接修改客户资料；LLM 与规则共用同一出口契约。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config import settings
from app.schemas import ActionDraft

TAG_ALIASES = {
    "红利": "dividend", "高股息": "dividend", "成长": "growth",
    "科技": "tech", "新能源": "new_energy", "债券": "bond",
    "宽基": "broad_index", "量化": "quant", "港股": "hk_equity",
}


def local_today() -> date:
    return __import__("datetime").datetime.now(ZoneInfo(settings.app_timezone)).date()


def extract_tags(text: str, explicit_only: bool = False,
                 tag_map: dict[str, str] | None = None) -> list[str]:
    """从文本提取主题标签；tag_map 允许传入用户自管理的标签字典（别名可自定义）。"""
    # For holding exposure do NOT infer tags from the product name. A manual tag is needed.
    if explicit_only:
        tag_match = re.search(r"(?:标签|风格)[:：]\s*([^，。；;\n]{1,100})", text)
        text = tag_match.group(1) if tag_match else ""
    mapping = tag_map if tag_map is not None else TAG_ALIASES
    tags = [tag for word, tag in mapping.items() if word in text]
    return list(dict.fromkeys(tags))


def parse_date(text: str, today: date | None = None) -> date | None:
    today = today or local_today()
    iso = re.search(r"\b(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?\b", text)
    if iso:
        try:
            return date(*map(int, iso.groups()))
        except ValueError:
            return None
    month_day = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text)
    if month_day:
        try:
            result = date(today.year, int(month_day.group(1)), int(month_day.group(2)))
            if result < today:
                result = result.replace(year=today.year + 1)
            return result
        except ValueError:
            return None
    days = re.search(r"(?:再过|下次|每)?\s*(\d{1,3})\s*天\s*(?:后|回访|跟进|提醒)?", text)
    if days and any(x in text for x in ("后", "下次", "提醒", "回访", "跟进", "每")):
        return today + timedelta(days=int(days.group(1)))
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text:
        return today + timedelta(days=1)
    weekday = re.search(r"下周([一二三四五六日天])", text)
    if weekday:
        idx = "一二三四五六日".index(weekday.group(1).replace("天", "日"))
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return monday + timedelta(days=idx)
    if "下周" in text:
        return today - timedelta(days=today.weekday()) + timedelta(days=7)
    if "今天" in text:
        return today
    return None


def is_due_query(message: str) -> bool:
    return bool(re.search(r"(?:谁|哪些|什么|列出|查看|查询|看下|看看).{0,18}"
                          r"(?:待|要|该|需要).{0,6}(?:回访|跟进|拜访|联系)", message)
                or re.search(r"(?:本周|今天|未来\d+天).{0,12}(?:任务|待办|跟进清单)", message))


def due_query_end(message: str, today: date | None = None) -> date:
    today = today or local_today()
    if "本周" in message:
        return today + timedelta(days=6 - today.weekday())
    m = re.search(r"未来\s*(\d{1,3})\s*天", message)
    if m:
        return today + timedelta(days=int(m.group(1)))
    if "明天" in message:
        return today + timedelta(days=1)
    return today


def extract_amount(text: str) -> Decimal | None:
    match = re.search(r"金额\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(万元|万|元)?", text)
    if not match:
        return None
    number = Decimal(match.group(1))
    return number * 10000 if match.group(2) in ("万元", "万") else number


def extract_need(text: str) -> str | None:
    m = re.search(r"需求\s*[:：]\s*([^，。；;\n]{2,180})", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:对|关注)([^，。；;]{2,70}?)(?:感兴趣|比较关注)", text)
    if m:
        return m.group(1).strip()
    return None


def extract_product(text: str) -> str | None:
    m = re.search(r"(?:买了|购买了|持有|配置了)\s*([^，。；;\n]{2,160})", text)
    if not m:
        return None
    value = re.split(r"金额\s*[:：]?", m.group(1), maxsplit=1)[0].strip()
    return value or None


def build_drafts(message: str, customer_name: str, contact_name: str | None,
                 today: date | None = None,
                 tag_map: dict[str, str] | None = None
                 ) -> tuple[list[ActionDraft], list[str]]:
    """目标客户/联系人已由上层确定；这里只做文本 -> 候选操作。"""
    today = today or local_today()
    drafts: list[ActionDraft] = []
    warnings: list[str] = []
    visit = bool(re.search(r"拜访了?|见了?|沟通了?|电话联系了?|交流了?", message))
    if visit:
        occurred = today - timedelta(days=1) if "昨天" in message else today
        drafts.append(ActionDraft(
            kind="log_visit", customer_name=customer_name, contact_name=contact_name,
            occurred_on=occurred,
            summary_md=f"## 原始沟通记录\n\n{message.strip()}",
        ))
    need_text = extract_need(message)
    if need_text:
        drafts.append(ActionDraft(
            kind="record_need", customer_name=customer_name, contact_name=contact_name,
            description=need_text, tags=extract_tags(need_text, tag_map=tag_map),
        ))
    product_name = extract_product(message)
    uncertain_purchase = bool(re.search(
        r"(?:可能|据说|听说|也许|不确定|尚未确认|准备|打算|计划|考虑|没有|没|尚未|未)"
        r"[^，。；;\\n]{0,12}(?:买了|购买了|持有|配置了)", message
    ))
    if product_name and uncertain_purchase:
        warnings.append("购买或持仓表述包含否定/意向/不确定性，未记录为持仓，请人工澄清")
    elif product_name:
        drafts.append(ActionDraft(
            kind="record_holding", customer_name=customer_name, contact_name=contact_name,
            product_name=product_name, amount_cny=extract_amount(message),
            tags=extract_tags(message, explicit_only=True, tag_map=tag_map),
        ))
    if re.search(r"提醒|下次|回访|跟进|再联系|再拜访", message):
        due = parse_date(message, today)
        if due:
            cadence = re.search(r"每\s*(\d{1,3})\s*天", message)
            drafts.append(ActionDraft(
                kind="create_followup", customer_name=customer_name,
                contact_name=contact_name, due_on=due,
                cadence_days=int(cadence.group(1)) if cadence else None,
                summary_md=f"根据沟通安排：{message.strip()}",
            ))
        else:
            warnings.append("检测到跟进意图但未识别明确日期，未创建跟进任务")
    return drafts, warnings
