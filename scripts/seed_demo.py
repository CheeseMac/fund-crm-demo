"""写入演示数据：租户 demo + 3 个账号 + 4 个客户（机构2/个人2）+ 同事隔离对照客户。

全部为虚构数据，仅用于本地演示。重复执行前先 --reset。
账号名可用环境变量/.env 自定义（.env 不入库，个人账号信息不进 git）：
    DEMO_SALES_USER / DEMO_ADMIN_USER / DEMO_COLLEAGUE_USER / DEMO_PASSWORD
用法：
    python -m scripts.seed_demo            # 不存在则写入
    python -m scripts.seed_demo --reset    # 清空 demo 租户后重写
"""
import os
import sys
from datetime import date, timedelta

from sqlalchemy import select, delete
from sqlalchemy.orm import sessionmaker

from app.database import engine, SessionLocal
from app.models import (
    Tenant, User, Customer, Contact, Visit, Need, Holding, FollowUp,
    MarketSignal, Alert, PendingAction, AuditLog,
    TaskSchedule, JobRun, OutboxEvent, UserNotificationPrefs,
)
from app.security import hash_password
from app.dependencies import Actor
from app.services.market import reconcile_market_signal, reconcile_due_tasks
from app.workers.scheduling import ensure_default_schedule
from app.application.notifications import get_or_create_prefs


def _load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_env()

TENANT_SLUG = "demo"
TENANT_NAME = "启元基金销售一部（虚构演示）"
SALES_USER = os.getenv("DEMO_SALES_USER", "sales01")
ADMIN_USER = os.getenv("DEMO_ADMIN_USER", "admin01")
COLLEAGUE_USER = os.getenv("DEMO_COLLEAGUE_USER", "colleague01")
PASSWORD = os.getenv("DEMO_PASSWORD", "Demo@2026#Pass")  # 演示口令，仅本地虚构环境
TODAY = date.today()


def reset(db):
    tenant = db.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
    if not tenant:
        return
    tid = tenant.id
    for model in (Alert, PendingAction, AuditLog, OutboxEvent, JobRun, TaskSchedule,
                  UserNotificationPrefs, MarketSignal, FollowUp,
                  Holding, Need, Visit, Contact, Customer, User):
        db.execute(delete(model).where(model.tenant_id == tid))
    db.execute(delete(Tenant).where(Tenant.id == tid))
    db.commit()
    print(f"已清空旧租户 {TENANT_SLUG} 的全部数据")


def add_customer(db, tid, owner, name, ctype, tags, background_md):
    c = Customer(tenant_id=tid, owner_user_id=owner, name=name,
                 customer_type=ctype, tags=tags, background_md=background_md)
    db.add(c)
    db.flush()
    return c


def add_contact(db, tid, owner, customer_id, name, title, notes_md=""):
    ct = Contact(tenant_id=tid, owner_user_id=owner, customer_id=customer_id,
                 name=name, title=title, notes_md=notes_md)
    db.add(ct)
    db.flush()
    return ct


def add_visit(db, tid, owner, customer_id, contact_id, occurred_on, channel, body_md):
    v = Visit(tenant_id=tid, owner_user_id=owner, customer_id=customer_id,
              contact_id=contact_id, occurred_on=occurred_on,
              channel=channel, body_md=body_md)
    db.add(v)


def add_need(db, tid, owner, customer_id, contact_id, description, tags,
             category="product_interest", status="open", details_md=""):
    n = Need(tenant_id=tid, owner_user_id=owner, customer_id=customer_id,
             contact_id=contact_id, status=status, category=category,
             description=description, tags=tags, details_md=details_md)
    db.add(n)


def add_holding(db, tid, owner, customer_id, product_name, fund_code,
                amount, as_of, tags, note_md):
    h = Holding(tenant_id=tid, owner_user_id=owner, customer_id=customer_id,
                product_name=product_name, fund_code=fund_code,
                amount_cny=amount, as_of=as_of, tags=tags,
                source="sales_reported_unverified", verified=False,
                note_md=note_md)
    db.add(h)


def add_task(db, tid, owner, customer_id, contact_id, due_on, cadence, reason):
    f = FollowUp(tenant_id=tid, owner_user_id=owner, customer_id=customer_id,
                 contact_id=contact_id, due_on=due_on, status="open",
                 cadence_days=cadence, reason_md=reason)
    db.add(f)


def main():
    reset_requested = "--reset" in sys.argv
    with SessionLocal() as db:
        if not reset_requested and db.scalar(
                select(Tenant.id).where(Tenant.slug == TENANT_SLUG)):
            print("租户 demo 已存在；如需重写请运行: python -m scripts.seed_demo --reset")
            return
        if reset_requested:
            reset(db)

        tenant = Tenant(slug=TENANT_SLUG, name=TENANT_NAME)
        db.add(tenant)
        db.flush()
        tid = tenant.id

        admin = User(tenant_id=tid, email=ADMIN_USER,
                     password_hash=hash_password(PASSWORD), role="admin")
        sales = User(tenant_id=tid, email=SALES_USER,
                     password_hash=hash_password(PASSWORD), role="sales")
        colleague = User(tenant_id=tid, email=COLLEAGUE_USER,
                         password_hash=hash_password(PASSWORD), role="sales")
        db.add_all([admin, sales, colleague])
        db.flush()
        owner = sales.id

        # V2：为每个用户建默认计划（每日 08:30 到期提醒）与通知偏好
        for user in (admin, sales, colleague):
            ensure_default_schedule(db, tid, user.id)
            db.add(UserNotificationPrefs(
                tenant_id=tid, user_id=user.id,
                timezone="Asia/Shanghai", lead_days=0,
                channels=["inbox"], digest_mode="instant"))
        db.commit()

        # ── 客户1：华星资管（机构，红利主题，市场事件命中）──
        hx = add_customer(db, tid, owner, "华星资管", "institution",
                          ["institution", "asset_mgmt"], """# 客户背景

## 机构定位
- 中型保险资管，固收为主，权益仓位谨慎
- 与本公司关系：研究交流约半年，产品合作意向初步

## 沟通偏好
- 投资总监王敏偏好数据化材料， dislikes 营销话术
- 每月一次线上交流，重大风格变化时希望主动沟通

## 未核实信息
- 口述持有红利ETF约50万，待正式对账确认""")
        wang = add_contact(db, tid, owner, hx.id, "王敏", "投资总监",
                           "- 决策链：王敏牵头，交易部李雷执行")
        li = add_contact(db, tid, owner, hx.id, "李雷", "交易主管",
                         "- 关注交易成本与容量，倾向ETF工具型产品")
        add_visit(db, tid, owner, hx.id, wang.id, TODAY - timedelta(days=21),
                  "meeting", """# 首次拜访纪要（示例数据）

## 背景
- 线下拜访，介绍公司固收团队与红利策略产品线

## 客户观点（未经验证）
- 权益部分只配低波动策略，回撤容忍度约5%
- 关注分红频率与容量，不喜欢规模暴涨的产品

## 待办
- 补充红利低波产品历史全周期数据""")
        add_visit(db, tid, owner, hx.id, wang.id, TODAY - timedelta(days=6),
                  "call", """# 电话会议纪要（示例数据）

## 背景
- 讨论红利策略容量、费率与分红机制

## 客户观点（未经验证）
- 认可研究框架，但要求提供同业对比数据
- 提到已在别处配置红利ETF（见持仓申报，未核验）

## 待办
- 整理合规通过的红利低波资料包；30天内回访""")
        add_need(db, tid, owner, hx.id, wang.id,
                 "关注红利策略及回撤控制，希望补充低波红利产品资料",
                 ["dividend"], details_md="客户明确要求全周期回撤与同业对比数据")
        add_need(db, tid, owner, hx.id, wang.id,
                 "需要基金公司风控体系与偿付说明材料",
                 [], category="service", status="open",
                 details_md="机构准入通用材料，走合规通道提供")
        add_holding(db, tid, owner, hx.id, "红利ETF", "510880", 500000,
                    TODAY - timedelta(days=6), ["dividend"],
                    "客户口述持有（在别处购买），未核验；仅作沟通参考")
        add_task(db, tid, owner, hx.id, wang.id,
                 TODAY + timedelta(days=2), 30, "补充红利低波产品资料后回访（周期回访30天）")

        # ── 客户2：蓝湾银行（机构，债券主题，含一条已逾期任务）──
        lw = add_customer(db, tid, owner, "蓝湾银行", "institution",
                          ["institution", "bank"], """# 客户背景

## 机构定位
- 城商行，代销准入评估中
- 关注入选产品线：债券指数基金、固收+

## 沟通偏好
- 总经理陈静把关准入，产品经理周涛负责材料
- 正式公文往来为主，材料需合规盖章版本""")
        chen = add_contact(db, tid, owner, lw.id, "陈静", "金融同业部总经理")
        zhou = add_contact(db, tid, owner, lw.id, "周涛", "产品经理",
                           "- 负责准入材料清单与尽调问卷")
        add_visit(db, tid, owner, lw.id, chen.id, TODAY - timedelta(days=16),
                  "meeting", """# 代销准入沟通纪要（示例数据）

## 背景
- 线下拜访，沟通代销合作框架

## 客户观点（未经验证）
- 债基与固收+为第一优先级，权益类暂缓
- 需要基金公司提供偿付能力、风控与运作报告

## 待办
- 按准入清单补充材料；已约定提交后跟进""")
        add_need(db, tid, owner, lw.id, chen.id,
                 "代销准入评估：债券指数基金与固收+产品线",
                 ["bond"], details_md="准入材料清单已发出，等待补充合规版本")
        add_holding(db, tid, owner, lw.id, "政金债ETF", "511520", 2000000,
                    TODAY - timedelta(days=16), ["bond"],
                    "同业持仓申报（口述），未核验")
        add_task(db, tid, owner, lw.id, zhou.id,
                 TODAY - timedelta(days=2), 30, "提交准入补充材料后跟进（已逾期，用于演示到期提醒）")

        # ── 客户3：张伟明（个人，宽基定投）──
        zw = add_customer(db, tid, owner, "张伟明", "individual", ["retail", "dingtou"],
                          """# 客户背景

- 35岁上班族，每月定投宽基指数
- 风险测评：积极型（示例标注，非系统判定）
- 关注定投纪律，不喜欢频繁操作""")
        add_visit(db, tid, owner, zw.id, None, TODAY - timedelta(days=13),
                  "wechat", """# 线上沟通纪要（示例数据）

- 想加大定投金额，担心短期高位
- 提醒：定投金额决策由客户自主决定，不做择时建议
- 约定执行满一个月后回顾""")
        add_need(db, tid, owner, zw.id, None,
                 "每月定投宽基指数，关注沪深300相关低费率产品",
                 ["broad_index"])
        add_holding(db, tid, owner, zw.id, "沪深300ETF", "510300", 80000,
                    TODAY - timedelta(days=30), ["broad_index"],
                    "客户口述持仓，未核验")
        add_task(db, tid, owner, zw.id, None,
                 TODAY + timedelta(days=15), None, "定投计划执行满月后回访")

        # ── 客户4：刘芳（个人，科技主题，未命中事件以演示克制）──
        lf = add_customer(db, tid, owner, "刘芳", "individual", ["retail"],
                          """# 客户背景

- 新客户，对科技成长方向感兴趣
- 风险承受能力待正式测评（演示数据）""")
        add_visit(db, tid, owner, lf.id, None, TODAY - timedelta(days=3),
                  "meeting", """# 面谈纪要（示例数据）

- 表达对AI产业方向的兴趣，问"能不能上车"
- 明确告知：先做风险测评，不做收益承诺与择时建议
- 待办：完成风险测评后再沟通主题产品资料""")
        add_need(db, tid, owner, lf.id, None,
                 "关注科技成长方向，风险承受能力待评估",
                 ["tech"], category="service")
        add_task(db, tid, owner, lf.id, None,
                 TODAY + timedelta(days=3), None, "风险测评完成后沟通科技主题产品资料")

        # ── 同事李强的客户：演示"同租户销售互相不可见" ──
        ht = add_customer(db, tid, colleague.id, "恒泰证券", "institution",
                          ["institution", "broker"], "# 同事李强名下客户（演示隔离）")
        add_contact(db, tid, colleague.id, ht.id, "郑华", "机构业务部负责人")
        add_need(db, tid, colleague.id, ht.id, None,
                 "量化对冲产品尽调需求（李强客户）", ["quant"])

        db.commit()

        # ── 市场事件（管理员发布，虚构数据）→ 触发标签匹配提醒 ──
        signals = [
            MarketSignal(tenant_id=tid, external_key="DEMO-STYLE-DIVIDEND-001",
                         kind="style_rotation", severity=4, tags=["dividend"],
                         headline="演示事件：红利风格交易拥挤度升至高位（虚构数据）",
                         details_md="演示用虚构事件：匹配持有/关注红利主题客户的销售，提示人工复核沟通需求，不构成任何交易建议。",
                         evidence_url="https://example.org/demo-dividend",
                         happened_on=TODAY),
            MarketSignal(tenant_id=tid, external_key="DEMO-RATE-BOND-001",
                         kind="risk", severity=3, tags=["bond"],
                         headline="演示事件：债券利率波动加大（虚构数据）",
                         details_md="演示用虚构事件：匹配债券主题客户，提示核对历史风险承受沟通记录。",
                         evidence_url="https://example.org/demo-bond",
                         happened_on=TODAY),
        ]
        db.add_all(signals)
        db.commit()
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        for s in signals:
            created = reconcile_market_signal(factory, tid, s.id, admin.id)
            print(f"市场事件 [{s.headline}] 生成提醒 {created} 条")

        # ── 到期任务提醒（主演示账号的逾期任务物化为站内提醒）──
        with SessionLocal() as db2:
            actor = Actor(user_id=owner, tenant_id=tid, role="sales")
            n = reconcile_due_tasks(db2, actor)
            print(f"到期任务提醒生成 {n} 条")

        print(f"""
演示数据写入完成（全部虚构）
────────────────────────────
登录页只需账号+密码（登录页自动使用 demo 租户）
管理员       : {ADMIN_USER}（发布市场事件、建账号）
销售(主演示) : {SALES_USER}（4个客户都挂这个号）
销售(对照)   : {COLLEAGUE_USER}（名下1个客户，演示互相不可见）
密码统一     : {PASSWORD}
账号名/密码可用 .env 中 DEMO_SALES_USER 等变量自定义（.env 不入库）
客户         : 华星资管(机构/红利)、蓝湾银行(机构/债券/有逾期任务)、
               张伟明(个人/宽基)、刘芳(个人/科技) + 同事的恒泰证券
""")


if __name__ == "__main__":
    main()
