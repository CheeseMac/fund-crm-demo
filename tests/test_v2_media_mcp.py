"""PR3 媒体安全、PR4 会议占位、PR6 MCP 受限入口的回归测试（docs/blueprints/acceptance_tests.md）。"""
import json

from tests.conftest import register, create_customer_and_contacts

WAV_BYTES = (b"RIFF\x24\x08\x00\x00WAVEfmt " + b"\x00" * 32)
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _token_of(header):
    return header["Authorization"].split(" ", 1)[1]


def _mcp(client, token, payload):
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
    resp = client.post("/mcp/", headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── PR3：媒体上传/扫描/票据下载 ──

def test_media_upload_ready_access_download(client):
    h = register(client, "media-a", "ma@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    init = client.post("/api/v2/media/uploads/initiate", headers=h, json={
        "customer_id": cid, "filename": "meeting.wav",
        "content_type": "audio/wav", "declared_size": len(WAV_BYTES)})
    assert init.status_code == 201, init.text
    asset_id = init.json()["asset_id"]
    up = client.put(f"/api/v2/media/{asset_id}/upload", headers=h, content=WAV_BYTES)
    assert up.status_code == 200 and up.json()["state"] == "ready", up.text
    meta = client.get(f"/api/v2/media/{asset_id}", headers=h).json()
    assert meta["sha256"] and meta["size_bytes"] == len(WAV_BYTES)
    # 列表可见
    listing = client.get("/api/v2/media", headers=h, params={"customer_id": cid})
    assert listing.status_code == 200 and len(listing.json()) == 1
    # 票据下载：内容一致；坏票据 401
    access = client.get(f"/api/v2/media/{asset_id}/access", headers=h)
    assert access.status_code == 200 and "download_url" in access.json()
    dl = client.get(access.json()["download_url"])
    assert dl.status_code == 200 and dl.content == WAV_BYTES
    bad = client.get(f"/api/v2/media/{asset_id}/download?token=12345.deadbeef", headers=h)
    assert bad.status_code == 401


def test_media_fake_mime_rejected_and_unreadable(client):
    h = register(client, "media-b", "mb@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    init = client.post("/api/v2/media/uploads/initiate", headers=h, json={
        "customer_id": cid, "filename": "fake.wav",
        "content_type": "audio/wav", "declared_size": len(PNG_BYTES)})
    asset_id = init.json()["asset_id"]
    up = client.put(f"/api/v2/media/{asset_id}/upload", headers=h, content=PNG_BYTES)
    assert up.status_code == 422 and "安全扫描" in up.json()["detail"]
    # 未通过扫描：拒绝签发票据
    assert client.get(f"/api/v2/media/{asset_id}/access", headers=h).status_code == 403


def test_media_oversize_and_bad_type_rejected(client):
    h = register(client, "media-c", "mc@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    r = client.post("/api/v2/media/uploads/initiate", headers=h, json={
        "customer_id": cid, "filename": "huge.wav", "content_type": "audio/wav",
        "declared_size": 300 * 1024 * 1024})
    assert r.status_code == 413
    r2 = client.post("/api/v2/media/uploads/initiate", headers=h, json={
        "customer_id": cid, "filename": "x.exe", "content_type": "application/x-msdownload",
        "declared_size": 100})
    assert r2.status_code == 415


def test_media_pending_upload_not_readable(client):
    h = register(client, "media-d", "md@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    init = client.post("/api/v2/media/uploads/initiate", headers=h, json={
        "customer_id": cid, "filename": "later.wav",
        "content_type": "audio/wav", "declared_size": 1024})
    assert init.status_code == 201
    assert client.get(f"/api/v2/media/{init.json()['asset_id']}/access",
                      headers=h).status_code == 403


def test_media_cross_user_isolated(client):
    ha = register(client, "media-e1", "me1@example.test")
    cid, _ = create_customer_and_contacts(client, ha)
    init = client.post("/api/v2/media/uploads/initiate", headers=ha, json={
        "customer_id": cid, "filename": "a.wav",
        "content_type": "audio/wav", "declared_size": len(WAV_BYTES)})
    asset_id = init.json()["asset_id"]
    client.put(f"/api/v2/media/{asset_id}/upload", headers=ha, content=WAV_BYTES)
    access = client.get(f"/api/v2/media/{asset_id}/access", headers=ha).json()
    # 另一个销售：元数据 404、且无法为他人素材签发票据（ACL 在签发时校验）
    hb = register(client, "media-e2", "me2@example.test")
    create_customer_and_contacts(client, hb, name="乙客户")
    assert client.get(f"/api/v2/media/{asset_id}", headers=hb).status_code == 404
    assert client.get(f"/api/v2/media/{asset_id}/access", headers=hb).status_code == 404
    # 下载凭票据即可（浏览器直开），无票据 401
    assert client.get(f"/api/v2/media/{asset_id}/download?token=x.y").status_code == 401


# ── PR4：会议占位 ──

def test_meeting_create_and_extract_placeholder(client):
    h = register(client, "meet-a", "mt@example.test")
    cid, _ = create_customer_and_contacts(client, h)
    init = client.post("/api/v2/media/uploads/initiate", headers=h, json={
        "customer_id": cid, "filename": "call.wav",
        "content_type": "audio/wav", "declared_size": len(WAV_BYTES)})
    asset_id = init.json()["asset_id"]
    client.put(f"/api/v2/media/{asset_id}/upload", headers=h, content=WAV_BYTES)
    m = client.post("/api/v2/meetings", headers=h, json={
        "customer_id": cid, "title": "红利策略交流", "occurred_on": "2026-10-29",
        "asset_ids": [asset_id]})
    assert m.status_code == 201 and m.json()["linked_assets"] == 1, m.text
    mid = m.json()["id"]
    detail = client.get(f"/api/v2/meetings/{mid}", headers=h).json()
    assert detail["assets"][0]["filename"] == "call.wav"
    assert detail["minutes_status"] == "none"
    ex = client.post(f"/api/v2/meetings/{mid}/extract-draft", headers=h)
    assert ex.status_code == 501 and "PR4" in ex.json()["detail"]


# ── PR6：MCP 受限入口 ──

def _initialize(client, token):
    return _mcp(client, token, {
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2026-07-28", "capabilities": {},
                   "clientInfo": {"name": "pytest", "version": "0"}}})


def test_mcp_rejects_missing_token(client):
    resp = client.post("/mcp/", headers={
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp.status_code == 401


def test_mcp_tools_list_and_scoped_search(client):
    h = register(client, "mcp-a", "mca@example.test")
    token = _token_of(h)
    create_customer_and_contacts(client, h)  # 华星资管
    _initialize(client, token)
    listed = _mcp(client, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in listed["result"]["tools"]]
    assert "customers_search" in names and "assistant_confirm_actions" in names
    called = _mcp(client, token, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "customers_search", "arguments": {"query": "华星"}}})
    text = called["result"]["content"][0]["text"]
    data = json.loads(text)
    assert data["customers"] and data["customers"][0]["name"] == "华星资管"


def test_mcp_prepare_and_confirm_with_approval_token(client):
    h = register(client, "mcp-b", "mcb@example.test")
    token = _token_of(h)
    cid, contacts = create_customer_and_contacts(client, h)
    _initialize(client, token)
    prep = _mcp(client, token, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "assistant_prepare_actions",
                   "arguments": {"message": "今天拜访了王敏，需求：关注红利低波产品；30天后回访"}}})
    data = json.loads(prep["result"]["content"][0]["text"])
    assert data["status"] == "confirmation_required"
    pid, approval = data["pending_action_id"], data["approval_token"]
    # 确认前未落库
    assert client.get(f"/customers/{cid}/visits", headers=h).json() == []
    # 错误令牌被拒
    bad = _mcp(client, token, {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "assistant_confirm_actions",
                   "arguments": {"pending_action_id": pid, "approval_token": "forged"}}})
    assert bad["result"]["isError"] is True
    assert client.get(f"/customers/{cid}/visits", headers=h).json() == []
    # 正确令牌：一次性，落库成功
    ok = _mcp(client, token, {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "assistant_confirm_actions",
                   "arguments": {"pending_action_id": pid, "approval_token": approval}}})
    assert ok["result"]["isError"] is False
    visits = client.get(f"/customers/{cid}/visits", headers=h).json()
    assert len(visits) == 1
    # 令牌用后即焚（重复确认走 REST 幂等路径，但令牌不可复用做新提案）
    replay = _mcp(client, token, {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "assistant_confirm_actions",
                   "arguments": {"pending_action_id": pid, "approval_token": approval}}})
    # 已执行提案重放：返回幂等结果（REST 层语义）
    assert replay["result"]["isError"] is False


def test_mcp_cross_owner_denied(client):
    ha = register(client, "mcp-c1", "mc1@example.test")
    hb = register(client, "mcp-c2", "mc2@example.test")
    cid_a, _ = create_customer_and_contacts(client, ha)
    create_customer_and_contacts(client, hb, name="乙客户")
    _initialize(client, _token_of(hb))
    called = _mcp(client, _token_of(hb), {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "customers_get_context",
                   "arguments": {"customer_id": cid_a}}})
    assert called["result"]["isError"] is True
