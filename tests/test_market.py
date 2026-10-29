from datetime import date
from tests.conftest import register, create_customer_and_contacts

def test_market_alert_tag_match_and_dedup(client):
    h = register(client, "alpha", "a@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    # Purchase is unverified; exposure tags are explicitly supplied by the sales user.
    ir = client.post("/assistant/interpret", headers=h, json={
        "message": "华星资管持有中证红利ETF，金额50万元，标签：红利"
    })
    assert ir.status_code == 200, ir.text
    assert ir.json()["preview"][0]["kind"] == "record_holding"
    client.post(f"/assistant/confirm/{ir.json()['pending_action_id']}", headers=h)
    holdings = client.get(f"/customers/{cid}/holdings", headers=h).json()
    assert len(holdings) == 1
    assert holdings[0]["verified"] is False
    assert holdings[0]["amount_cny"] == "500000.00"
    assert holdings[0]["tags"] == ["dividend"]
    event = {
        "external_key": "DEMO-2026-10-28-dividend-01",
        "kind": "style_rotation", "severity": 4, "tags": ["dividend"],
        "headline": "演示：红利风格发生明显变化（非真实行情）",
        "happened_on": "2026-10-28",
        "details_md": "本事件仅用于测试规则系统。",
        "evidence_url": "https://example.org/mock-event",
    }
    r = client.post("/market/signals", headers=h, json=event)
    assert r.status_code == 201, r.text
    assert r.json()["alerts_created"] == 1
    alert_id = client.get("/alerts", headers=h).json()[0]["id"]
    assert client.post(f"/alerts/{alert_id}/ack", headers=h).json()["status"] == "acknowledged"
    r2 = client.post(f"/market/signals/{r.json()['id']}/reconcile", headers=h)
    assert r2.status_code == 200
    assert r2.json()["alerts_created"] == 0
    assert client.post("/market/signals", headers=h, json=event).status_code == 409

def test_low_severity_no_alert(client):
    h = register(client, "delta", "d@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    r = client.post(f"/customers/{cid}/holdings", headers=h, json={
        "product_name": "测试基金", "as_of": "2026-10-28", "tags": ["bond"],
    })
    assert r.status_code == 201
    event = {
        "external_key": "DEMO-low-severity", "kind": "risk", "severity": 2,
        "tags": ["bond"], "headline": "演示：测试债券波动",
        "happened_on": "2026-10-28",
    }
    assert client.post("/market/signals", headers=h, json=event).json()["alerts_created"] == 0
