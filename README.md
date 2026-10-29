# fund-crm-demo · 基金销售 AI CRM 演示环境

对下载目录方案包 `fund_sales_ai_crm_starter.zip`（公募基金销售客户维护助手，FastAPI + SQLAlchemy 参考实现）的落地部署：已在本机跑通、灌好演示数据、可直接拿去演示。

**全部客户、持仓、市场事件均为虚构数据，仅用于演示。** 方案包原始说明见 [`docs/00_方案包原始README.md`](docs/00_方案包原始README.md)。

## 快速开始

```bash
cd workspace/fund-crm-demo   # 进入本项目根目录
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

或直接双击 `start_server.bat`。然后打开：

- 工作台（演示用）：http://127.0.0.1:8000/ —— 顶部导航四页：**工作台（秘书对话）/ 客户管理 / 待办事项 / 提醒中心**
- Swagger 接口文档：http://127.0.0.1:8000/docs

各页能力：

- **工作台**：整页不滚动、输入框常驻；顶部统计条（待办/逾期/未读提醒/客户数）可点击跳页；三个快捷示例按钮。
- **客户管理**：客户列表（可搜索）+ 完整档案页（背景可编辑、机构联系人、需求台账可关闭、拜访记录、口述持仓申报），支持导出 `.md` 档案；新建类表单全部折叠收起。
- **待办事项**：按已逾期/今天/未来/已完成分组；可视化新建待办（客户/联系人/到期日/循环周期）；一键完成，循环任务自动排下一期。
- **提醒中心**：提醒列表按 待处理/市场事件/回访到期 筛选并逐条确认；**定时提醒设置**（到期提前 N 天，点立即同步生效）；管理员专属**市场事件发布**卡片（中文主题自动映射规范标签，发布即按客户持仓/需求匹配生成提醒）。

## 演示账号（登录页只需账号 + 密码）

登录页已定制简化：不出现"租户"概念（页面内部自动使用 `demo` 租户），账号直接填、密码直接填即可。

| 账号 | 角色 | 用途 | 密码 |
|---|---|---|---|
| `sales01` | 销售 | **主演示账号**，4 个客户都挂它名下 | `Demo@2026#Pass` |
| `admin01` | 管理员 | 发布市场事件、建员工账号 | `Demo@2026#Pass` |
| `colleague01` | 销售 | 对照账号，演示"同事互相不可见" | `Demo@2026#Pass` |

账号名/口令可在本地 `.env`（不入库）用 `DEMO_SALES_USER` / `DEMO_ADMIN_USER` / `DEMO_COLLEAGUE_USER` / `DEMO_PASSWORD` 自定义后重新 `seed_demo --reset`——个人账号信息不进 git。

演示剧本、话术、每步预期效果见 [`演示手册.md`](演示手册.md)。

## 演示数据概览（均为虚构）

sales01 名下 4 个客户：

| 客户 | 类型 | 主题标签 | 看点 |
|---|---|---|---|
| 华星资管 | 机构（王敏/李雷两联系人） | dividend | 红利持仓+需求，命中"红利风格拥挤"事件；有完整拜访史 |
| 蓝湾银行 | 机构（陈静/周涛） | bond | 债券主题；**有一条已逾期回访任务**，演示到期提醒 |
| 张伟明 | 个人 | broad_index | 宽基定投客户 |
| 刘芳 | 个人 | tech | 无持仓，演示"市场事件不乱匹配"的克制性 |

同事李强（colleague）名下另有恒泰证券（quant）——sales01 登录看不到它。

预置 2 条虚构市场事件（红利风格拥挤 s4、债券利率波动 s3）已触发匹配提醒，登录后"关注提醒"面板直接可见。

## 文件清单

- `app/`、`alembic/`、`frontend/`、`tests/`、`方案完整版.md` — 方案包原始代码与设计文档；本环境定制：
  - `app/ai/`（V2：解析规则/实体匹配/提供者端口与适配器）、`app/application/`（V2：统一业务用例）、`app/workers/`（V2：调度/作业/Outbox）
  - `app/api.py` 路由委托 Application 层并新增 `/api/v2/*` 端点；`alembic/versions/0002_v2_scheduling.py`
  - `frontend/index.html` 重写为四页导航工作台
- `docs/05_架构演进设计.md` + `docs/blueprints/` — V2 评审意见与演进方案（contracts.py 端口契约、worker_claim.sql、API/验收清单）
- `tests/test_v2_scheduling.py` — PR2 回归（偏好持久化、Outbox 幂等、计划物化/作业执行）
- `tests/test_v2_media_mcp.py` — PR3/PR6 回归（媒体安全矩阵、票据下载、会议占位、MCP 鉴权/越权/审批令牌）
- `app/mcp_entry.py` — MCP 入口（JWT 中间件 + 工具 + 审批令牌）；`app/services/object_store.py` — 本地私有对象存储
- `scripts/seed_demo.py` — **本环境新增**：演示数据种子脚本（`--reset` 可重置）
- `scripts/demo_flow.py` — 方案包自带的端到端 API 演示脚本（另建一次性租户，不影响 demo 数据）
- `crm_demo.db` — SQLite 数据库（演示数据已灌入）
- `.env` — 本地开发配置（DEV_MODE=true、随机 JWT 密钥；正式部署须换 PostgreSQL+RLS，见 docs/04）
- `start_server.bat` — 一键启动（库不存在时会自动迁移+灌种子）
- `演示手册.md` — 演示剧本

## 常用运维

```bash
# 重置演示数据（清空 demo 租户重写）
.venv/Scripts/python.exe -m scripts.seed_demo --reset

# 跑测试（SQLite 子集，PG RLS 用例自动跳过）
.venv/Scripts/python.exe -m pytest

# 方案包自带演示流（一次性租户，不影响 demo 数据）
.venv/Scripts/python.exe -m scripts.demo_flow
```

注意：`crm_demo.db` 删除后需 `alembic upgrade head` + `seed_demo` 重建。

## 当前状态与待办

- [x] 环境搭建（Python 3.12 venv、依赖、迁移）2026-10-28
- [x] 演示数据（租户/3 账号/5 客户/拜访/需求/持仓/任务/市场事件/提醒）
- [x] 全接口 curl 验证 + UI 浏览器实测（登录、自然语言预览-确认、提醒面板）
- [x] 测试套件 22 passed / 1 skipped（PG RLS 用例需 PostgreSQL，本地跳过）
- [x] 登录页定制（2026-10-28）：只填账号+密码、隐藏租户概念与开发注册提示，主演示账号 `sales01`
- [x] 布局改版（2026-10-28）：整页不滚动、聊天输入框常驻可视区；低频的"新建客户/联系人"折叠进左栏抽屉；示例句改为可点击快捷按钮；右上角徽章可切换账号
- [x] 功能补齐（2026-10-28）：四页导航（工作台/客户管理/待办事项/提醒中心）；客户档案页（联系人/需求/拜访/持仓/导出md）；待办分组+新建+完成；提醒筛选+提前提醒设置+管理员UI发布市场事件；后端配套 GET /tasks 与 lead_days
- [x] **V2 PR1**（2026-10-29，见 [`docs/05_架构演进设计.md`](docs/05_架构演进设计.md)）：AI 解析拆分为 `app/ai`（text_rules / entity_match / ports / rule·llm adapters），业务用例上移 `app/application`（assistant / tasks / market / notifications），路由变薄；旧 API 与 9 项测试完全兼容
- [x] **V2 PR2**（2026-10-29）：自主触发落地——新表 `task_schedules / job_runs / outbox_events / user_notification_prefs`（迁移 0002）；提醒设置从浏览器 localStorage 改为**服务端持久化**（跨设备一致，`PUT /api/v2/me/notification-preferences`）；市场事件发布改为**事务性 Outbox**（同事务写事件 + 幂等派发，HTTP 内同步完成一次，失败由调度循环重试）；API 进程内嵌 Scheduler/Runner（`SCHEDULER_ENABLED`，默认开，30s 一轮：物化计划→执行作业→派发事件），每日 08:30 自动生成到期提醒；作业与事件可用 `GET /api/v2/jobs`、`GET /api/v2/outbox`（管理员）观测
- [x] **V2 PR3 多媒体存储**（2026-10-29）：`media_assets`/`media_processing_jobs`（迁移 0003）+ 本地私有对象存储（`media_storage/` 不入库，接口对齐 MediaStore 端口）；上传白名单（录音/录像/图片/PDF/Markdown，≤200MB）+ 魔数交叉校验（伪造 MIME 拒绝）+ 未扫描不可读 + 短时 HMAC 票据下载（S3 预签名语义，浏览器可直开）；客户详情页新增"素材与会议"区块
- [x] **V2 PR4 会议（占位）**（2026-10-29）：`meetings` 建档与素材关联可用；`extract-draft` 明确返回 501 未实现说明（转写/纪要待接入 Transcriber/Summarizer 端口，绝不伪造内容）
- [x] **V2 PR6 MCP 受限入口**（2026-10-29）：`/mcp/` Streamable HTTP（mcp 2.x，stateless）+ Bearer JWT 鉴权中间件；9 个工具（customers_search/get_context、followups_list_due、alerts_list、visits_list、assistant_prepare/confirm_actions、alerts_acknowledge、media_request_upload）全部复用 Application 用例；高风险确认需**一次性审批令牌**（15 分钟、用后即焚、重放幂等）；跨 owner 一律拒绝
- [ ] V2 后续（docs/05 §11）：PR5 市场数据接入与确定性周报（SQL 统计报告 + 可选 AI 叙述）；PR4 转写/纪要正式实现
- [ ] 演示后如需接真实数据：换 PostgreSQL + RLS 部署（方案包 docker-compose 已备，见 `docs/04_开发部署与验收.md`）

## 边界（演示时可向听众说明）

- 系统是内部客户关系维护工具原型，**不是交易平台**；"持仓"均为销售口述申报（`verified=false`），不代表交易确认。
- 市场事件为人工录入的虚构样例，无实时行情接入；提醒只提示"人工复核客户沟通需求"，不给买卖建议。
- 自然语言当前走离线规则（有限句式），LLM 通道默认关闭（`LLM_ENABLED=false`），审批后才可启用。
