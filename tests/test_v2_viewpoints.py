"""市场观点与用户标签字典回归：CRUD/隔离、标签交集关联客户、自定义别名进入自然语言解析。"""
from datetime import date, timedelta

from app.ai.text_rules import local_today

from tests.conftest import register, create_customer_and_contacts


def test_viewpoint_crud_and_isolation(client):
    h = register(client, "vp-a", "vpa@example.test")
    created = client.post("/api/v2/viewpoints", headers=h, json={
        "viewpoint_date": local_today().isoformat(),
        "author_type": "colleague", "author_name": "研究部老王",
        "content_md": "红利拥挤度偏高，注意回撤。", "tags": ["dividend"],
        "source_note": "晨会"})
    assert created.status_code == 201, created.text
    vid = created.json()["id"]
    listing = client.get("/api/v2/viewpoints", headers=h).json()
    assert len(listing) == 1 and listing[0]["author_name"] == "研究部老王"
    # 按标签筛选
    tagged = client.get("/api/v2/viewpoints", headers=h,
                        params={"tag": "dividend"}).json()
    assert len(tagged) == 1
    assert client.get("/api/v2/viewpoints", headers=h,
                      params={"tag": "tech"}).json() == []
    # 更新与删除
    patched = client.patch(f"/api/v2/viewpoints/{vid}", headers=h,
                           json={"content_md": "更新后的观点", "tags": ["bond"]})
    assert patched.json()["tags"] == ["bond"]
    hb = register(client, "vp-b", "vpb@example.test")
    assert client.get("/api/v2/viewpoints", headers=hb).json() == []  # 互相不可见
    assert client.delete(f"/api/v2/viewpoints/{vid}", headers=hb).status_code == 404
    assert client.delete(f"/api/v2/viewpoints/{vid}", headers=h).json()["deleted"] is True


def test_viewpoint_matched_customers(client):
    h = register(client, "vp-c", "vpc@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    # 需求挂 dividend、持仓挂 bond
    client.post(f"/customers/{cid}/holdings", headers=h, json={
        "product_name": "政金债ETF", "amount_cny": 100000,
        "as_of": local_today().isoformat(), "tags": ["bond"]})
    ir = client.post("/assistant/interpret", headers=h, json={
        "message": "今天拜访了王敏，需求：关注红利低波；30天后回访"})
    client.post(f"/assistant/confirm/{ir.json()['pending_action_id']}", headers=h)
    vid = client.post("/api/v2/viewpoints", headers=h, json={
        "viewpoint_date": local_today().isoformat(), "author_type": "self",
        "content_md": "红利与债券都要小心。", "tags": ["dividend", "bond"]}).json()["id"]
    matched = client.get(f"/api/v2/viewpoints/{vid}/matched-customers", headers=h).json()
    assert matched["tags"] == ["bond", "dividend"]
    assert len(matched["customers"]) == 1
    reasons = "；".join(matched["customers"][0]["reasons"])
    assert "红利低波" in reasons and "政金债ETF" in reasons
    # 无交集标签的观点：不关联任何客户
    vid2 = client.post("/api/v2/viewpoints", headers=h, json={
        "viewpoint_date": local_today().isoformat(), "author_type": "self",
        "content_md": "港股流动性观察。", "tags": ["hk_equity"]}).json()["id"]
    m2 = client.get(f"/api/v2/viewpoints/{vid2}/matched-customers", headers=h).json()
    assert m2["customers"] == []


def test_tag_management_and_custom_alias_parsing(client):
    h = register(client, "vp-d", "vpd@example.test")
    tags = client.get("/api/v2/tags", headers=h).json()
    keys = {t["key"] for t in tags}
    assert {"dividend", "bond", "tech"} <= keys  # 内置默认已初始化
    # 新增自定义标签（带别名）
    new_tag = client.post("/api/v2/tags", headers=h, json={
        "key": "china_value", "label": "中特估", "aliases": ["央企价值", "央国企"]})
    assert new_tag.status_code == 201, new_tag.text
    # 别名立即进入自然语言解析：标签：央企价值 → china_value
    cid, _ = create_customer_and_contacts(client, h)
    ir = client.post("/assistant/interpret", headers=h, json={
        "message": "华星资管持有某央企价值基金，金额10万元，标签：央企价值"})
    assert ir.status_code == 200
    preview = ir.json()["preview"][0]
    assert preview["kind"] == "record_holding"
    assert preview["tags"] == ["china_value"], preview["tags"]
    # 键冲突/非法键
    assert client.post("/api/v2/tags", headers=h, json={
        "key": "china_value", "label": "重复"}).status_code == 409
    assert client.post("/api/v2/tags", headers=h, json={
        "key": "Bad Key", "label": "非法"}).status_code == 422
    # 修改别名后解析随之更新
    tid = new_tag.json()["id"]
    client.patch(f"/api/v2/tags/{tid}", headers=h,
                 json={"aliases": ["国企改革"]})
    ir2 = client.post("/assistant/interpret", headers=h, json={
        "message": "华星资管持有某国企改革基金，金额5万元，标签：国企改革"})
    assert ir2.json()["preview"][0]["tags"] == ["china_value"]
    # 删除
    assert client.delete(f"/api/v2/tags/{tid}", headers=h).json()["deleted"] is True
    hb = register(client, "vp-e", "vpe@example.test")
    assert client.delete(f"/api/v2/tags/{tags[0]['id']}",
                         headers=hb).status_code == 404  # 字典按用户隔离
