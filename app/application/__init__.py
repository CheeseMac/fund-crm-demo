"""Application 层（docs/05 §8）：REST / MCP / Job 统一调用的业务用例。

路由保持薄封装：鉴权（Actor）之后只做参数校验与调用；
任何写入仍需经过 PendingAction 确认或明确的服务端校验与审计。
"""
