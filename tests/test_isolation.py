from tests.conftest import register, create_customer_and_contacts

def test_two_layer_isolation(client):
    a = register(client, "team-a", "admin-a@example.test")
    b = register(client, "team-b", "admin-b@example.test")
    cid, contacts = create_customer_and_contacts(client, a)
    assert len(client.get("/customers", headers=a).json()) == 1
    assert client.get("/customers", headers=b).json() == []
    assert client.get(f"/customers/{cid}", headers=b).status_code == 404
    assert client.get(f"/customers/{cid}/contacts", headers=b).status_code == 404
    assert client.post("/tasks", headers=b, json={
        "customer_id": cid, "due_on": "2026-12-31"
    }).status_code == 404

    r = client.post("/users", headers=a, json={
        "email": "teammate@example.test", "password": "ColleaguePassword#2026",
        "role": "sales",
    })
    assert r.status_code == 201, r.text
    login = client.post("/auth/login", json={
        "tenant_slug": "team-a", "email": "teammate@example.test",
        "password": "ColleaguePassword#2026",
    })
    teammate = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/customers", headers=teammate).json() == []
    assert client.get(f"/customers/{cid}", headers=teammate).status_code == 404
    assert client.post("/assistant/interpret", headers=teammate,
                       json={"message": "今天拜访了王敏"}).status_code == 422
    assert client.get("/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401

def test_duplicate_contact_same_customer(client):
    h = register(client, "alpha", "a@example.test")
    cid, contacts = create_customer_and_contacts(client, h)
    r = client.post(f"/customers/{cid}/contacts", headers=h,
                    json={"name": "王敏"})
    assert r.status_code == 409
