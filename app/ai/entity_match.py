"""实体定位与权限内解析：把候选草稿绑定到当前销售可见的客户/联系人。

歧义必须澄清，绝不"按概率选择"（docs/05 §6）；所有解析结果仍要走
PendingAction 人工确认才落库。
"""
from __future__ import annotations

from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import Actor
from app.models import Customer, Contact
from app.schemas import ActionDraft, ResolvedAction


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


def choose_target(message: str, customer_names: list[str],
                  contact_pairs: list[tuple[str, str]]):
    """在给定名字集合中定位目标；contact_pairs = [(联系人名, 所属客户名)]。"""
    customer_matches = [n for n in customer_names if n in message]
    contact_matches = [p for p in contact_pairs if p[0] in message]
    if contact_matches:
        longest = max(len(p[0]) for p in contact_matches)
        contact_matches = [p for p in contact_matches if len(p[0]) == longest]
        if customer_matches:
            contact_matches = [p for p in contact_matches if p[1] in customer_matches]
        if len(contact_matches) != 1:
            raise HTTPException(409, "联系人重名，请同时说出所属机构或客户全名")
        return contact_matches[0][1], contact_matches[0][0]
    if customer_matches:
        longest = max(customer_matches, key=len)
        matches = [n for n in customer_matches if len(n) == len(longest)]
        if len(matches) != 1:
            raise HTTPException(409, "客户名称有歧义，请明确客户全名")
        return longest, None
    raise HTTPException(422, "没有匹配到你有权限的客户或联系人，请先创建客户并使用完整姓名")


def recognize_target(message: str, customers: list[Customer], contacts: list[Contact]):
    customer_by_id = {c.id: c for c in customers}
    pairs = [(c.name, customer_by_id[c.customer_id].name) for c in contacts]
    return choose_target(message, [c.name for c in customers], pairs)


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
