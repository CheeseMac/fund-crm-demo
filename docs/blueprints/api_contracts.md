# V2 API 和 MCP 接口边界速查

本文是接口**草案**，需要按现有 FastAPI 实际 schema/权限做最终落地。

| Use Case | HTTP | MCP | 权限 |
|---|---|---|---|
| 列出我的待办 | GET `/tasks/due` | `followups.list_due` | `crm:task:read` |
| 查客户档案 | GET `/customers/{id}` | `customers.get_context` | `crm:customer:read` |
| 查询提醒 | GET `/alerts` | `alerts.list` | `crm:alert:read` |
| 自然语言提案 | POST `/assistant/interpret` | `assistant.prepare_actions` | `crm:draft:write` |
| 确认提案 | POST `/assistant/confirm/{id}` | `assistant.confirm_actions` | 强制一次性用户审批 |
| 获取上传凭据 | POST `/api/v2/media/uploads/initiate` | `media.request_upload` | `crm:media:write` |
| 获取已生成周报 | GET `/api/v2/reports` | `reports.get_weekly` | `crm:report:read` |
| 设置自动周报 | POST `/api/v2/schedules` | `schedules.prepare_create` | 创建计划需用户确认 |
| 添加市场数据 | POST `/internal/market/observations` | 默认不暴露 | 服务身份、来源校验 |
| 发送外部对客消息 | 不提供直接调用 | 不暴露 | 单独合规审批工作流 |

## 业务调用必须完成的 7 项核验

1. 校验可信身份、Token audience/issuer、用户启用状态、授权 scopes。
2. 确定 `tenant_id`/`owner_user_id`，不接受请求中的任意 owner 作为授权。
3. 校验目标客户、联系人及媒体资源的 parent-relationship 和 ACL。
4. 对数据修改执行明确的业务校验/乐观锁/唯一性检查。
5. 高影响操作先 PendingAction，二次确认需**服务端验证**的短时批准。
6. 业务写入与 Outbox 在同一事务提交；异步 Worker 幂等消费。
7. 留下谁通过何种入口完成操作的审计；返回数据按许可字段最小化。

## 兼容约束

- V1 路由保留，由相同 Application Service 处理。
- MCP tools 不能拥有自己一份绕过业务 Service 的 ORM 实现。
- Agent 的工具描述不能声称支持实时行情，除非已接入合法且具时效的来源。
- MCP 不负责主动“叫醒”用户；它可以查询已由 CRM 内部产生的提醒。
- MCP 本身不传送大录屏文件；使用 CRM 颁发受限上传票据。
