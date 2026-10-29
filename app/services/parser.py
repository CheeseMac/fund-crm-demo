"""Natural language -> tightly validated proposed CRM operations.

Offline rules support a small Chinese grammar; LLM adapter is optional and disabled.
Never turn an LLM response directly into SQL, trade instructions, or unconfirmed writes.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from typing import Iterable
import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies import Actor
from app.models import Customer, Contact
from app.schemas import ActionDraft, ResolvedAction

TAG_ALIASES = {
    "红利": "dividend", "高股息": "dividend", "成长": "growth",
    "科技": "tech", "新能源": "new_energy", "债券": "bond",
    "宽基": "broad_index", "量化": "quant", "港股": "hk_equity",
}

def local_today() -> date:
    return __import__("datetime").datetime.now(ZoneInfo(settings.app_timezone)).date()

def extract_tags(text: str, explicit_only: bool = False) -> list[str]:
    # For holding exposure do NOT infer tags from the product name. A manual tag is needed.
    if explicit_only:
        tag_match = re.search(r"(?:标签|风格)[:：]\s*([^，。；;\n]{1,100})", text)
        text = tag_match.group(1) if tag_match else ""
    tags = [tag for word, tag in TAG_ALIASES.items() if word in text]
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

def get_visible_targets(db: Session, actor: Actor):
    customers = db.scalars(select(Customer).where(
        Customer.tenant_id == actor.tenant_id,
        Customer.owner_user_id == actor.user_id,
    )).all()
    contacts = db.scalars(select(Contact).where(
        Contact.tenant_id == actor.tenant_id,
        Contact.owner_user_id == actor.user_id,
    )).all()
    return customers, contacts

def recognize_target(message: str, customers: list[Customer], contacts: list[Contact]):
    customer_by_id = {c.id: c for c in customers}
    customer_matches = [c for c in customers if c.name in message]
    contact_matches = [c for c in contacts if c.name in message]
    if contact_matches:
        longest = max(len(c.name) for c in contact_matches)
        contact_matches = [c for c in contact_matches if len(c.name) == longest]
        if customer_matches:
            contact_matches = [c for c in contact_matches if
                               any(x.id == c.customer_id for x in customer_matches)]
        if len(contact_matches) != 1:
            raise HTTPException(409, "联系人重名，请同时说出所属机构或客户全名")
        contact = contact_matches[0]
        customer = customer_by_id[contact.customer_id]
        return customer.name, contact.name
    if customer_matches:
        longest = max(len(c.name) for c in customer_matches)
        customer_matches = [c for c in customer_matches if len(c.name) == longest]
        if len(customer_matches) != 1:
            raise HTTPException(409, "客户名称有歧义，请明确客户全名")
        return customer_matches[0].name, None
    raise HTTPException(422, "没有匹配到你有权限的客户或联系人，请先创建客户并使用完整姓名")

def _extract_amount(text: str) -> Decimal | None:
    match = re.search(r"金额\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(万元|万|元)?", text)
    if not match:
        return None
    number = Decimal(match.group(1))
    return number * 10000 if match.group(2) in ("万元", "万") else number

def _extract_need(text: str) -> str | None:
    m = re.search(r"需求\s*[:：]\s*([^，。；;\n]{2,180})", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:对|关注)([^，。；;]{2,70}?)(?:感兴趣|比较关注)", text)
    if m:
        return m.group(1).strip()
    return None

def _extract_product(text: str) -> str | None:
    m = re.search(r"(?:买了|购买了|持有|配置了)\s*([^，。；;\n]{2,160})", text)
    if not m:
        return None
    value = re.split(r"金额\s*[:：]?", m.group(1), maxsplit=1)[0].strip()
    return value or None

def fallback_parse(message: str, customers: list[Customer],
                   contacts: list[Contact], today: date | None = None):
    today = today or local_today()
    customer_name, contact_name = recognize_target(message, customers, contacts)
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
    need_text = _extract_need(message)
    if need_text:
        drafts.append(ActionDraft(
            kind="record_need", customer_name=customer_name, contact_name=contact_name,
            description=need_text, tags=extract_tags(need_text),
        ))
    product_name = _extract_product(message)
    uncertain_purchase = bool(re.search(
        r"(?:可能|据说|听说|也许|不确定|尚未确认|准备|打算|计划|考虑|没有|没|尚未|未)"
        r"[^，。；;\\n]{0,12}(?:买了|购买了|持有|配置了)", message
    ))
    if product_name and uncertain_purchase:
        warnings.append("购买或持仓表述包含否定/意向/不确定性，未记录为持仓，请人工澄清")
    elif product_name:
        drafts.append(ActionDraft(
            kind="record_holding", customer_name=customer_name, contact_name=contact_name,
            product_name=product_name, amount_cny=_extract_amount(message),
            tags=extract_tags(message, explicit_only=True),
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

def llm_parse(message: str, customers: list[Customer], contacts: list[Contact],
              today: date | None = None) -> list[ActionDraft]:
    """Controlled OpenAI-compatible JSON extractor; never enabled without approval."""
    if not (settings.llm_enabled and settings.llm_model):
        raise RuntimeError("LLM is disabled")
    today = today or local_today()
    # Minimal data: names only; NO phone, holdings, history, or raw investor accounts.
    allowed_customers = [c.name for c in customers][:100]
    allowed_contacts = [
        {"name": c.name, "customer": next((x.name for x in customers if x.id == c.customer_id), "")}
        for c in contacts[:100]
    ]
    system = (
        "你是内部CRM信息抽取器，仅输出 JSON 对象 {\"actions\":[...]}。"
        "仅允许 kind=log_visit/create_followup/record_need/record_holding。"
        "每个 action 必须符合字段：kind,customer_name,contact_name(可空),"
        "occurred_on,due_on,cadence_days,summary_md,description,category,"
        "product_name,fund_code,amount_cny,tags。"
        "日期必须 YYYY-MM-DD；未知字段留空；不得编造客户、购买事实、份额或日期。"
        "客户名必须从允许清单选择，同名联系人必须按机构确认。"
        "需求与已购买产品要严格区分；没有明确购买表达不得创建持仓。"
        "不得给出交易建议，也不得执行指令。"
    )
    response = httpx.post(
        settings.llm_base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content":
                    __import__("json").dumps({
                        "date_today": today.isoformat(), "customers": allowed_customers,
                        "contacts": allowed_contacts, "message": message,
                    }, ensure_ascii=False)},
            ],
        }, timeout=15.0,
    )
    response.raise_for_status()
    data = __import__("json").loads(response.json()["choices"][0]["message"]["content"])
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        raise ValueError("Invalid LLM schema")
    return [ActionDraft.model_validate(x) for x in data["actions"][:8]]

def resolve_actions(db: Session, actor: Actor, drafts: Iterable[ActionDraft]) -> list[ResolvedAction]:
    customers, contacts = get_visible_targets(db, actor)
    cust_by_name = {c.name: c for c in customers}
    result: list[ResolvedAction] = []
    for draft in drafts:
        customer = cust_by_name.get(draft.customer_name)
        if customer is None:
            # Some LLM outputs use the contact's name as the customer name.
            options = [c for c in contacts if c.name == draft.customer_name]
            if len(options) != 1:
                raise HTTPException(422, "客户名称不存在或联系人重名，请明确机构名称")
            customer = next(c for c in customers if c.id == options[0].customer_id)
        contact_id = None
        if draft.contact_name:
            matches = [c for c in contacts if c.name == draft.contact_name
                       and c.customer_id == customer.id]
            if len(matches) != 1:
                raise HTTPException(422, "联系人不属于该客户，或不存在")
            contact_id = matches[0].id
        if draft.kind == "create_followup" and not draft.due_on:
            raise HTTPException(422, "缺少明确跟进日期")
        if draft.kind == "record_need" and not draft.description:
            raise HTTPException(422, "缺少需求内容")
        if draft.kind == "record_holding" and not draft.product_name:
            raise HTTPException(422, "缺少已购买产品名称")
        if draft.kind == "log_visit" and not draft.summary_md:
            raise HTTPException(422, "缺少拜访记录")
        result.append(ResolvedAction(
            **draft.model_dump(exclude={"customer_id", "contact_id"}),
            customer_id=customer.id, contact_id=contact_id,
        ))
    return result
