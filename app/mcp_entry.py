"""MCP 受限入口（docs/05 §7）：外部 Agent 经 Streamable HTTP + Bearer JWT 访问。

- 鉴权：复用平台 JWT（演示简化；生产应换公司 OIDC/OAuth audience 校验）；
  Actor 由服务端从 token 解出，绝不接受工具参数里的 owner/tenant；
- 工具只做只读查询 + 草稿提案 + 低风险操作，全部复用 Application 用例，
  没有任何绕过业务服务的私有 ORM 路径；
- 高风险写入（确认提案）要求一次性审批令牌（issue/verify 下方实现），
  "模型说用户同意了"不算批准；
- 未开放：任意 SQL、全租户导出、权限修改、删除审计、对客营销与交易。
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.dependencies import Actor
from app.models import User
from app.security import decode_token

CURRENT_ACTOR: contextvars.ContextVar[Actor | None] = contextvars.ContextVar(
    "mcp_actor", default=None)

APPROVAL_TTL_MINUTES = 15


def _tool_error(exc: Exception):
    """HTTP/校验异常转 MCP ToolError（返回 isError 结果而非协议错误）。"""
    from fastapi import HTTPException
    from mcp.server.mcpserver.exceptions import ToolError
    raise ToolError(str(exc.detail) if isinstance(exc, HTTPException) else str(exc))



def _actor() -> Actor:
    actor = CURRENT_ACTOR.get()
    if actor is None:
        raise RuntimeError("MCP 请求缺少已验证身份")
    return actor


def _db() -> Session:
    return SessionLocal()


class BearerAuthMiddleware:
    """ASGI 中间件：校验 Bearer JWT 并解析 Actor（401 拦截匿名/失效凭证）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        actor = None
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            try:
                user_id, tenant_id = decode_token(token)
                with _db() as db:
                    db.info.update(tenant_id=tenant_id, user_id=user_id)
                    user = db.scalar(select(User).where(
                        User.id == user_id, User.tenant_id == tenant_id))
                    if user is not None and user.is_active:
                        actor = Actor(user_id=user_id, tenant_id=tenant_id,
                                      role=user.role)
            except Exception:  # noqa: BLE001 - 任何凭证问题都按未认证处理
                actor = None
        if actor is None:
            await self._unauthorized(scope, receive, send)
            return
        CURRENT_ACTOR.set(actor)
        await self.app(scope, receive, send)

    @staticmethod
    async def _unauthorized(scope, receive, send):
        body = json.dumps({"error": "unauthorized",
                           "detail": "缺少或无效的 Bearer JWT"}).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


# ── 一次性审批令牌（docs/05 §7.2：服务端产生、短时、绑定提案、用后即焚）──

def issue_approval_token(db: Session, actor: Actor, pending_id: str) -> str:
    from app.models import PendingAction
    pending = db.scalar(select(PendingAction).where(
        PendingAction.id == pending_id,
        PendingAction.tenant_id == actor.tenant_id,
        PendingAction.owner_user_id == actor.user_id))
    if pending is None:
        raise ValueError("提案不存在")
    token = secrets.token_urlsafe(24)
    pending.payload = {**pending.payload, "approval": {
        "sha256": hashlib.sha256(token.encode()).hexdigest(),
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(minutes=APPROVAL_TTL_MINUTES)).isoformat()}}
    db.commit()
    return token


def consume_approval_token(db: Session, actor: Actor, pending_id: str,
                           token: str) -> None:
    from app.models import PendingAction
    pending = db.scalar(select(PendingAction).where(
        PendingAction.id == pending_id,
        PendingAction.tenant_id == actor.tenant_id,
        PendingAction.owner_user_id == actor.user_id))
    if pending is None:
        raise ValueError("提案不存在")
    if pending.status == "executed":
        # 已执行提案的重放：走幂等路径返回历史结果（令牌不可复用于新提案）
        return
    approval = (pending.payload or {}).get("approval")
    sha = hashlib.sha256(token.encode()).hexdigest()
    if not approval or approval.get("sha256") != sha:
        raise ValueError("审批令牌无效")
    if datetime.fromisoformat(approval["expires_at"]) < datetime.now(timezone.utc):
        raise ValueError("审批令牌已过期")
    payload = {**pending.payload}
    payload.pop("approval", None)
    pending.payload = payload
    db.commit()


def build_mcp_server():
    from mcp.server.mcpserver import MCPServer
    from app.application import assistant as assistant_app
    from app.application import media as media_app
    from app.dependencies import get_customer

    server = MCPServer(
        name="fund-crm-mcp",
        title="基金销售 CRM 受限入口",
        version="0.2.0",
        instructions=(
            "只读查询 + 需人工确认的草稿提案。所有数据仅限当前登录销售本人名下；"
            "写入必须先 prepare 再由用户凭一次性审批令牌 confirm。"
            "不提供交易、对客营销或任意 SQL 能力。"),
    )

    @server.tool()
    def customers_search(query: str) -> dict:
        """按名称模糊搜索当前销售自己的客户（只读，裁剪字段）。"""
        actor = _actor()
        with _db() as db:
            from app.models import Customer
            rows = db.scalars(select(Customer).where(
                Customer.tenant_id == actor.tenant_id,
                Customer.owner_user_id == actor.user_id,
                Customer.name.contains(query),
            ).limit(20)).all()
            return {"customers": [{"id": c.id, "name": c.name,
                                   "type": c.customer_type} for c in rows]}

    @server.tool()
    def customers_get_context(customer_id: str) -> dict:
        """读取一个客户的档案摘要（背景/标签），仅限本人名下客户。"""
        actor = _actor()
        try:
            with _db() as db:
                c = get_customer(db, actor, customer_id)
                return {"id": c.id, "name": c.name, "type": c.customer_type,
                        "tags": c.tags,
                        "background_excerpt": (c.background_md or "")[:800]}
        except Exception as exc:
            _tool_error(exc)

    @server.tool()
    def followups_list_due(days: int = 7) -> dict:
        """列出未来 N 天内到期/已逾期的跟进任务（只读）。"""
        actor = _actor()
        with _db() as db:
            until = datetime.now(timezone.utc).date() + timedelta(days=days)
            items = assistant_app.list_due(db, actor, until)
            return {"until": until.isoformat(), "items": items}

    @server.tool()
    def alerts_list() -> dict:
        """列出当前销售的站内提醒（只读）。"""
        actor = _actor()
        with _db() as db:
            from app.models import Alert
            rows = db.scalars(select(Alert).where(
                Alert.tenant_id == actor.tenant_id,
                Alert.owner_user_id == actor.user_id,
            ).order_by(Alert.created_at.desc()).limit(30)).all()
            return {"alerts": [{"id": a.id, "kind": a.kind, "status": a.status,
                                "reason_excerpt": a.reason_md[:200]} for a in rows]}

    @server.tool()
    def visits_list(customer_id: str) -> dict:
        """读取一个客户的拜访纪要列表（只读，正文截断）。"""
        actor = _actor()
        try:
            with _db() as db:
                get_customer(db, actor, customer_id)
                from app.models import Visit
                rows = db.scalars(select(Visit).where(
                    Visit.tenant_id == actor.tenant_id,
                    Visit.owner_user_id == actor.user_id,
                    Visit.customer_id == customer_id,
                ).order_by(Visit.occurred_on.desc()).limit(20)).all()
                return {"visits": [{"id": v.id, "occurred_on": v.occurred_on.isoformat(),
                                    "body_excerpt": v.body_md[:300]} for v in rows]}
        except Exception as exc:
            _tool_error(exc)

    @server.tool()
    def assistant_prepare_actions(message: str) -> dict:
        """把一句话解析为待确认的候选操作（提案，不写任何业务事实）。

        返回 pending_action_id 与一次性 approval_token；确认须调用
        assistant_confirm_actions，且令牌 15 分钟内有效、用后即焚。
        """
        actor = _actor()
        try:
            with _db() as db:
                result = assistant_app.interpret(db, actor, message)
            if result.get("status") != "confirmation_required":
                return result
            with _db() as db:
                token = issue_approval_token(db, actor, result["pending_action_id"])
        except Exception as exc:
            _tool_error(exc)
        result["approval_token"] = token
        result["notice"] = "提案未落库；确认需一次性审批令牌。"
        return result

    @server.tool()
    def assistant_confirm_actions(pending_action_id: str, approval_token: str) -> dict:
        """凭一次性审批令牌确认提案并落库（高风险写入，服务端校验）。"""
        actor = _actor()
        try:
            with _db() as db:
                consume_approval_token(db, actor, pending_action_id, approval_token)
                return assistant_app.confirm_actions(db, actor, pending_action_id)
        except Exception as exc:
            _tool_error(exc)

    @server.tool()
    def alerts_acknowledge(alert_id: str) -> dict:
        """把一条站内提醒标记为已查看（低风险，仍留审计）。"""
        actor = _actor()
        with _db() as db:
            from app.models import Alert, AuditLog
            alert = db.scalar(select(Alert).where(
                Alert.id == alert_id, Alert.tenant_id == actor.tenant_id,
                Alert.owner_user_id == actor.user_id))
            if alert is None:
                _tool_error(ValueError("提醒不存在"))
            alert.status = "acknowledged"
            db.add(AuditLog(tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
                            action="mcp_ack", resource_type="alert",
                            resource_id=alert.id, details_hash=alert.dedup_key[:63]))
            db.commit()
            return {"status": alert.status}

    @server.tool()
    def media_request_upload(customer_id: str, filename: str,
                             content_type: str, declared_size: int) -> dict:
        """为客户素材申请短时上传票据（受限写，白名单类型与大小校验）。"""
        actor = _actor()
        try:
            with _db() as db:
                return media_app.initiate_upload(db, actor, customer_id, filename,
                                                 content_type, declared_size)
        except Exception as exc:
            _tool_error(exc)

    return server


def build_mcp_asgi_app():
    """构建挂载到 /mcp 的 ASGI 应用（JWT 中间件包住 Streamable HTTP）。"""
    server = build_mcp_server()
    inner = server.streamable_http_app(
        streamable_http_path="/", json_response=True, stateless_http=True,
        host=None,  # 演示直连；生产经反代时应配置真实 Host 并启用校验
    )
    app = BearerAuthMiddleware(inner)
    app.mcp_starlette = inner  # lifespan 由 main.py 代为进入
    return app
