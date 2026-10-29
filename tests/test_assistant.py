from datetime import timedelta
from tests.conftest import register, create_customer_and_contacts
from app.services.parser import local_today

def test_natural_language_draft_confirm_and_replay(client):
    h = register(client, "alpha", "a@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    sentence = "今天拜访了王敏，需求：关注红利策略；30天后回访"
    r = client.post("/assistant/interpret", headers=h, json={"message": sentence})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["status"] == "confirmation_required"
    assert {x["kind"] for x in p["preview"]} == {
        "log_visit", "record_need", "create_followup",
    }
    assert client.get(f"/customers/{cid}/visits", headers=h).json() == []
    result = client.post(f"/assistant/confirm/{p['pending_action_id']}", headers=h)
    assert result.status_code == 200, result.text
    assert len(result.json()["items"]) == 3
    again = client.post(f"/assistant/confirm/{p['pending_action_id']}", headers=h)
    assert again.json()["idempotent_replay"] is True
    assert len(client.get(f"/customers/{cid}/visits", headers=h).json()) == 1
    assert len(client.get(f"/customers/{cid}/needs", headers=h).json()) == 1
    due = (local_today() + timedelta(days=31)).isoformat()
    assert len(client.get("/tasks/due", headers=h, params={"until": due}).json()) == 1
    exported = client.get(f"/customers/{cid}/export.md", headers=h)
    assert exported.status_code == 200
    assert "今天拜访了王敏" in exported.text

def test_cadence_reminder_no_duplicates(client):
    h = register(client, "beta", "b@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    r = client.post("/assistant/interpret", headers=h,
                    json={"message": "请提醒我每30天回访华星资管"})
    assert r.status_code == 200, r.text
    preview = r.json()["preview"]
    assert preview[0]["cadence_days"] == 30
    client.post(f"/assistant/confirm/{r.json()['pending_action_id']}", headers=h)
    today = local_today()
    far = (today + timedelta(days=31)).isoformat()
    tasks = client.get("/tasks/due", headers=h, params={"until": far}).json()
    assert len(tasks) == 1
    task_id = tasks[0]["id"]
    assert client.post(f"/tasks/{task_id}/complete", headers=h).json()["next_task_id"]
    replay = client.post(f"/tasks/{task_id}/complete", headers=h)
    assert replay.json()["already_completed"] is True
    soon = client.post("/tasks", headers=h, json={
        "customer_id": cid, "due_on": today.isoformat(), "reason_md": "每月回访",
    }).json()
    assert soon["id"]
    first = client.post("/reminders/reconcile", headers=h).json()["alerts_created"]
    second = client.post("/reminders/reconcile", headers=h).json()["alerts_created"]
    assert first == 1 and second == 0

def test_unknown_customer_never_created_implicitly(client):
    h = register(client, "gamma", "g@example.test")
    r = client.post("/assistant/interpret", headers=h,
                    json={"message": "今天拜访了不存在的客户"})
    assert r.status_code == 422
    assert client.get("/customers", headers=h).json() == []


def test_customer_and_need_patch_and_frontend(client):
    h = register(client, "patch-team", "p@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    ui = client.get("/")
    assert ui.status_code == 200
    assert "CRM FIELDNOTES" in ui.text
    updated = client.patch(f"/customers/{cid}", headers=h,
                           json={"background_md": "## 偏好\\n长期跟踪", "tags": ["research"]})
    assert updated.status_code == 200
    assert "偏好" in updated.json()["background_md"]
    draft = client.post("/assistant/interpret", headers=h,
                        json={"message": "王敏的需求：关注债券产品"})
    # The simple rule extractor requires the known contact name, which is present.
    assert draft.status_code == 200
    confirmed = client.post(
        f"/assistant/confirm/{draft.json()['pending_action_id']}", headers=h
    )
    assert confirmed.status_code == 200
    need_id = confirmed.json()["items"][0]["id"]
    patch = client.patch(f"/needs/{need_id}", headers=h,
                         json={"status":"fulfilled","details_md":"客户已经收到审批材料"})
    assert patch.status_code == 200
    assert patch.json()["status"] == "fulfilled"


def test_uncertain_purchase_not_saved_as_holding(client):
    h = register(client, "uncertain", "uncertain@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    for sentence in ["听说华星资管可能买了红利ETF", "华星资管计划买了红利ETF"]:
        r = client.post("/assistant/interpret", headers=h, json={"message":sentence})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "needs_clarification"
    assert client.get(f"/customers/{cid}/holdings", headers=h).json() == []
