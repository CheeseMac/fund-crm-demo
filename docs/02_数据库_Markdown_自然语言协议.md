# 数据建模、Markdown 边界及自然语言 Agent 协议

## 1. 先判断一个字段是否必须结构化

**可以作为日期、状态、权限、聚合、提醒、关联、对账条件的字段，必须结构化。** 例如“下次联系时间”，不应只出现在散文里。  
**需要保留人物语气、观点来龙去脉、上下文或不规则叙事的材料，用 Markdown。** 例如“为什么客户担心回撤”。

| 业务数据 | 结构化表/字段 | Markdown 内容 | 备注 |
|---|---|---|---|
| 客户基本身份 | customers.name/type/owner/tags | background_md | 单个人或机构，不能混成一个大字符串 |
| 机构联系人 | contacts.customer_id/name/title | notes_md | 一机构多联系人 |
| 拜访 | visits.customer_id/contact_id/occurred_on/channel | body_md | 写入时间不从正文检索 |
| 需求 | needs.status/category/description/tags | details_md | 状态变化可控 |
| 持仓或购买陈述 | holdings.product_name/fund_code/amount_cny/as_of/source/verified/tags | note_md | 默认为未核验，不能替代交易记录 |
| 回访计划 | followups.due_on/status/cadence_days | reason_md | 系统定时操作靠日期字段 |
| 市场观察 | market_signals.kind/severity/happened_on/tags/external_key | details_md | 外部证据单独保留 URL |
| 待办提醒 | alerts.status/kind/signal_id/task_id/dedup_key | reason_md | 明确触发依据 |
| 待确认操作 | pending_actions.status/expires_at/payload(JSON) | payload 中的局部 Markdown | TTL 需要后续定期清除 |
| 审计 | audit_logs.tenant_id/owner/action/resource_id/details_hash | 不留详细客户正文 | 降低泄漏面 |

如果某一个自由文本属性将来变为业务检索条件（例如“投资经理是否有产品评审权限”），就应该增加结构化字段，而不是依赖不稳定的关键词匹配。

## 2. PostgreSQL 表和关键约束

| 表 | 主键 / 关联 | 关键索引和策略 |
|---|---|---|
| tenants | UUID 字符串 id | 唯一 slug |
| users | id → tenant_id | (tenant_id,email) 唯一；RLS 限制租户 |
| customers | id → tenant_id + owner | (tenant_id,owner,id) 唯一；同销售客户名唯一 |
| contacts | id → customer_id | FK(tenant,owner,customer)；客户内联系人名唯一 |
| visits | id → customer & primary contact | FK(tenant,owner,customer,contact)，最近拜访索引 |
| needs | id → customer/contact | 开放/已满足/已关闭状态 |
| holdings | id → customer | `as_of` 日期、`verified=false`，不等于真实仓位 |
| followups | id → customer/contact | (tenant,owner,status,due_on) 索引 |
| market_signals | id → tenant | (tenant,external_key) 唯一 |
| alerts | id → customer + optional signal/task | (tenant,owner,dedup_key) 唯一 |
| pending_actions | id → tenant + owner | 30 分钟过期，已执行返回结果 |
| audit_logs | id → tenant + owner | 仅追加，生产应外送不可变审计 |

**注意**：代码中的 UUID 在 ORM 示例使用 36 位字符串，便于 SQLite 与 PostgreSQL 兼容。正式性能优化可迁移到 PostgreSQL 原生 `UUID`，但属于需要 Alembic 数据迁移的 schema 变更。

### RLS 策略

项目迁移 `alembic/versions/0001_initial.py` 为拥有客户数据的表创建：
```sql
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers FORCE ROW LEVEL SECURITY;

CREATE POLICY scoped_access ON customers
FOR ALL TO crm_app
USING (
  tenant_id = nullif(current_setting('app.tenant_id', true), '')
  AND owner_user_id = nullif(current_setting('app.user_id', true), '')
)
WITH CHECK (
  tenant_id = nullif(current_setting('app.tenant_id', true), '')
  AND owner_user_id = nullif(current_setting('app.user_id', true), '')
);
```

数据库中使用**无超级用户、无 BYPASSRLS 权限**的 `crm_app` 应用角色。服务器验证 JWT 后，在每个 SQLAlchemy 事务启动时通过 `set_config(..., true)` 设定 scope。采用事务级变量而非连接级变量，避免连接池复用导致串租户。

仍须审查：所有后台作业、连接池、数据库备份、数据导出、管理员查询都要有边界；RLS 不是唯一安全措施。

### 交叉引用约束

示例：
```sql
FOREIGN KEY (tenant_id, owner_user_id, customer_id)
REFERENCES customers (tenant_id, owner_user_id, id)
```
这样可阻止“同租户的销售 A，创建一个自己名下的拜访但绑定销售 B 的客户”的错误。代码已为 `alerts.signal_id/task_id` 增加复合外键。真实 PG 下仍须对所有关联键、用户角色及后续的企业共享授权进行一致性测试。

## 3. Markdown 文档规范

### 客户背景 `customers.background_md`

```markdown
# 客户背景

## 机构定位
- 类型：资管机构
- 与本公司的业务关系：研究交流阶段

## 沟通偏好
- 喜欢每月一次资产配置交流
- 关注回撤控制与净值波动的解释

## 未核实信息
- 关于资金期限的说法仅源于交流，等待客户正式确认
```

### 拜访纪要 `visits.body_md`

```markdown
# 2026-10-28 客户交流纪要

## 本次背景
- 沟通方式：线下拜访
- 客户：华星资管
- 主联系人：王敏
- 议题：红利策略与组合波动

## 客户观点（未经验证）
- 更关注回撤而非短期排名
- 希望下次补充不同市场阶段表现的客观资料

## 市场背景
- 只填写有时间戳和可靠出处的信息；不要凭印象填数

## 我方可执行事项
- 30 天内回访
- 整理已通过公司合规审查的产品材料

## 风险与事实区分
- 任何金额、持仓、交易仅为访谈陈述，不能直接视为成交确认
```

生产版应保留：
- `source_type`（销售输入、录音转写、客户邮件、已核验系统）、创建时间、编辑版本、原始附件引用；
- `as_of` 日期与时区；
- 内容分级标签（公开/内部/限制/高度敏感）；
- 内容摘要哈希与导出水印。

如果允许上传 PDF、录音或名片，需要独立的数据处理目的及保留策略，不能将原始身份证、银行卡信息送往通用 LLM。

## 4. 自然语言 API 约定

### 4.1 新建草稿

`POST /assistant/interpret`，`Authorization: Bearer ...`：

```json
{
  "message": "今天拜访了王敏，需求：关注红利策略；30天后回访"
}
```

返回示意（ID、日期会根据实际环境变化）：

```json
{
  "status": "confirmation_required",
  "pending_action_id": "<uuid>",
  "source": "offline_rules",
  "warnings": [],
  "preview": [
    {
      "kind": "log_visit",
      "customer_name": "华星资管",
      "contact_name": "王敏",
      "occurred_on": "<today>",
      "summary_md": "## 原始沟通记录\\n\\n..."
    },
    {
      "kind": "record_need",
      "customer_name": "华星资管",
      "description": "关注红利策略",
      "tags": ["dividend"]
    },
    {
      "kind": "create_followup",
      "customer_name": "华星资管",
      "contact_name": "王敏",
      "due_on": "<today+30 days>"
    }
  ]
}
```

实际 JSON 会包含更多可选字段及解析后 ID。**返回预览并不意味着任何客户事实已写入。**

### 4.2 确认草稿

`POST /assistant/confirm/{pending_action_id}`（空请求体）。

返回：
```json
{
  "status": "executed",
  "idempotent_replay": false,
  "items": [
    {"kind": "log_visit", "id": "<uuid>"},
    {"kind": "record_need", "id": "<uuid>"},
    {"kind": "create_followup", "id": "<uuid>"}
  ]
}
```

重复确认返回 `idempotent_replay=true`。PostgreSQL 行锁避免并发重复操作，操作成功/失败以整笔事务提交/回滚为界。异常不会部分创建两条业务数据后把确认单标为成功。

### 4.3 直接回答查询

`POST /assistant/interpret`：
```json
{"message":"本周谁需要跟进"}
```
返回 `status="answered"`、`intent="query_due"`、`until`、`items`。与提案型写入不同，这类查询直接执行预置权限过滤的 SQL，而不是让模型输出任意 SQL。

### 4.4 有限的操作类型

| kind | 触发意图 | 数据修改 | 要求 |
|---|---|---|---|
| log_visit | 今天拜访/昨天沟通 | 添加拜访 | 授权客户 + 日期 + Markdown |
| record_need | 需求：/对…感兴趣 | 添加需求 | 有明确需求描述 |
| record_holding | 持有/买了/配置了 | 添加**未核验**申报记录 | 有明确产品名称 |
| create_followup | 30天后回访/下周二提醒/每30天 | 添加任务 | 有确定日期，循环周期可选 |
| query_due | 本周谁需要跟进 | 只读 | 只能查当前人可见任务 |

当前自然语言规则没有覆盖所有中文表达；LLM 网关支持增强理解，但仍须强制 `ActionDraft` Pydantic 校验、实体匹配和人工确认。自然语言编辑既有需求/联系人，推荐在二期引入“目标精确定位 + 变更 diff + 版本控制”；目前通过 REST `PATCH /needs/{id}` 等接口更新。

## 5. LLM 安全调用设计

服务入口位于 `app/services/parser.py::llm_parse`。

开启前必须满足：模型/供应商审批、客户信息处理合法性、数据流向及日志策略评估、接口认证、传输加密、输出评测。推荐公司内受控推理网关。默认：
```dotenv
LLM_ENABLED=false
```
开发时若已获批准：
```dotenv
LLM_ENABLED=true
LLM_BASE_URL=http://private-llm-gateway:8001/v1
LLM_API_KEY=<managed-secret>
LLM_MODEL=<approved-model-id>
```

防范措施：
- 每次只送本销售有权访问的**必要客户/联系人姓名**及当前待解析句子；不送整个租户历史。
- LLM 只能输出允许的 JSON schema，无 SQL 执行、网页浏览、基金交易、消息发送等工具权限。
- 下游 `resolve_actions` 重新检查客户属于当前销售、联系人属于当前客户。
- 未识别目标或同名冲突则澄清，不能“按概率选择”。
- LLM 提取出的金额/产品仍是未经核验的销售记录。
- 存在提示注入时，Markdown/用户原话视作数据，不能获得更高系统权限。
- 预览确认时要显式显示来源、字段缺失、低置信度、标签建议。
- 应把模型失败模式、回退行为、误报率纳入测试。当前回退规则适用有限句式，不要作为完整自然语言能力承诺。

## 6. 市场事件与客户关联

### 6.1 主题字典

统一英文技术 tag，例如：
`dividend`, `growth`, `tech`, `new_energy`, `bond`, `broad_index`, `quant`, `hk_equity`。

中文输入可以映射到主题字典，但**产品代码与真实主题暴露必须经过批准的产品主数据核实**。当前仅在用户明确提供 `标签：红利` 时将持仓标注 `dividend`，不能看到产品名称就自动推断分类。

注意：本 MVP 的 `holdings.as_of` 表示销售登记陈述所对应的观察日期，**不是基金实际买入日期或经托管机构核验的仓位时点**；历史语句如“去年买的”不应被自动解释为已确认的历史成交。

### 6.2 事件结构

```json
{
  "external_key": "DEMO-2026-10-28-001",
  "kind": "style_rotation",
  "severity": 4,
  "tags": ["dividend"],
  "headline": "演示事件：红利风格出现变化（非真实行情）",
  "details_md": "测试用途，不是行情观点。",
  "evidence_url": "https://example.org/mock-event",
  "happened_on": "2026-10-28"
}
```

规则：
```text
if severity < 3:
    ignore
for each tenant user, with this user's RLS:
    for each user's customer:
        if (holding.tags OR open_need.tags) intersects market_signal.tags:
            create at most one alert per (user, signal, customer)
```

生产版需增加：事件来源可信度、时效/过期、产品主题权重、真实持仓复核、冷却期、推送渠道与审批、频次上限、客户沟通合规内容模板。

### 6.3 场景示例

- 宽基/成长风格发生明显切换：向明确关注成长主题的客户销售显示“建议核对客户当前沟通需求”，不说“应当赎回”。
- 债券利率波动加大：对有债券主题记录的客户显示“核对历史风险承受能力及已核验产品资料”。
- 市场情绪低迷：显示“客户关怀和信息服务任务”，而非“抄底窗口”。
- 市场情绪高涨：提示“加强风险教育与信息披露”，而非鼓励追涨。

## 7. Markdown 检索与 RAG 的正确使用范围

**推荐检索路径**：
1. 明确时间、客户、任务、产品代码的查询 → SQL 强条件；
2. “上次为什么担心净值波动” → 检索可见客户的 Markdown；
3. SQL 先做 `tenant_id + owner_user_id + allowed_customer_ids` 过滤，再做全文或向量召回；
4. 生成答复附带 `visit_id`、拜访日期、可点击纪要引用；
5. 明确“来自客户表述 / 销售观点 / 官方数据源”，避免事实混淆。

不建议将所有租户客户 Markdown 放入单一没有行权限的全局向量索引，也不应通过向量相似度推断客户究竟持有什么基金。

本 MVP **没有安装 pgvector 或实现 RAG**。原因是首期的确定性任务/交易陈述、身份权限更重要，且向量数据复制会扩大隐私攻击面。

## 8. 数据演进建议

在真正上线前建议设计并落库：
- `products`, `product_tag_versions`, `verified_position_snapshots`；
- `visit_attendees`（一次拜访多位联系人）；
- `customer_acl`, `sharing_requests`, `teams`；
- `need_history`, `contact_history`, `customer_merges`；
- `outbox_events`, `job_executions`, `notification_deliveries`；
- `sources`, `market_signal_provenance`, `rule_versions`；
- `attachments`（授权扫描、存储、留存、权限）。

每个迁移都需考虑旧数据补齐、校验、索引创建成本、回滚边界与监管档案保存要求。

## 9. 日历与提醒语义

当前 `cadence_days` 使用**自然日**，不是中国 A 股交易日、工作日或基金开放日；`APP_TIMEZONE=Asia/Shanghai`。如果机构需要“每月最后一个交易日跟踪”“T+2 确认后回访”，必须接入经维护的交易日历和产品业务日历，不能用 `30 days` 近似。
