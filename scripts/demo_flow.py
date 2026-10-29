"""Full synthetic demo against a DEV-mode server; never touches actual market/trade feeds."""
import os
from uuid import uuid4
from datetime import date
import httpx

BASE = os.getenv("CRM_BASE_URL", "http://127.0.0.1:8000")
def main():
    slug = "demo-" + uuid4().hex[:8]
    with httpx.Client(base_url=BASE, timeout=15.0) as client:
        def call(method, path, token=None, payload=None):
            h = {"Authorization": f"Bearer {token}"} if token else {}
            r = client.request(method, path, headers=h, json=payload)
            r.raise_for_status()
            return r.json()
        auth = call("POST", "/auth/dev-register-tenant", payload={
            "tenant_slug": slug, "tenant_name": "虚构基金销售部门",
            "email": "demo@example.test", "password": "DemoPassword#2026",
        })
        token = auth["access_token"]
        institution = call("POST", "/customers", token, {
            "name": "华星资管", "customer_type": "institution",
        })
        cid = institution["id"]
        for name in ("王敏", "李雷"):
            call("POST", f"/customers/{cid}/contacts", token,
                 {"name": name, "title": "投资经理"})
        draft = call("POST", "/assistant/interpret", token, {
            "message": "今天拜访了王敏，需求：关注红利策略；30天后回访",
        })
        assert draft["status"] == "confirmation_required"
        first = call("POST", f"/assistant/confirm/{draft['pending_action_id']}", token)
        holding = call("POST", "/assistant/interpret", token, {
            "message": "华星资管持有中证红利ETF，金额50万元，标签：红利",
        })
        call("POST", f"/assistant/confirm/{holding['pending_action_id']}", token)
        event = call("POST", "/market/signals", token, {
            "external_key": "synthetic-" + uuid4().hex,
            "kind": "style_rotation", "severity": 4, "tags": ["dividend"],
            "headline": "演示用虚构市场风格变化（非真实行情）",
            "details_md": "模拟测试",
            "evidence_url": "https://example.org/mock-event",
            "happened_on": date.today().isoformat(),
        })
        alerts = call("GET", "/alerts", token)
        print("Demo tenant:", slug)
        print("Customer ID:", cid)
        print("Confirmed operations:", [x["kind"] for x in first["items"]])
        print("Market alerts created:", event["alerts_created"])
        print("Inbox alert count:", len(alerts))
        print("Done. No real customer or market data was used.")

if __name__ == "__main__":
    main()
