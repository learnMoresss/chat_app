"""A：草稿落库、整形 prefs、清理残渣、分句工具。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.shape import split_reply_parts


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "stability-a-secret")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def _auth(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "stabuser", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_split_reply_parts():
    parts = split_reply_parts("第一句。第二句！第三句？第四句。")
    assert 2 <= len(parts) <= 3
    assert "第一句" in parts[0]


def test_draft_and_prefs_and_residue(client: TestClient):
    h = _auth(client)
    sync = client.get("/api/v1/sync", headers=h).json()
    conv_id = sync["conversations"][0]["id"]

    patched = client.patch(
        f"/api/v1/conversations/{conv_id}",
        headers=h,
        json={"draft": "明天再说"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["draft"] == "明天再说"
    again = client.get("/api/v1/sync", headers=h).json()
    assert any(c["id"] == conv_id and c["draft"] == "明天再说" for c in again["conversations"])

    prefs = client.put(
        "/api/v1/prefs",
        headers=h,
        json={"shape_enabled": True, "split_send": True},
    )
    assert prefs.status_code == 200, prefs.text
    assert prefs.json()["split_send"] is True
    got = client.get("/api/v1/prefs", headers=h).json()
    assert got["shape_enabled"] is True
    assert got["split_send"] is True

    # 塞一条壳消息再清理
    from app import db
    from app.security import iso, new_id

    uid = again["me"]["id"]
    mid = new_id("m_")
    db.execute(
        """
        INSERT INTO messages(id, client_id, seq, user_id, conv_id, sender_id, mine, kind, text, state, created_at, context_skip)
        VALUES (?, ?, 99, ?, ?, 'bot', 0, 'text', ?, 'sent', ?, 0)
        """,
        (mid, new_id("cli_"), uid, conv_id, '[{"type":"text","text":"壳"}]', iso()),
    )
    cleaned = client.post(f"/api/v1/conversations/{conv_id}/clean-residue", headers=h)
    assert cleaned.status_code == 200, cleaned.text
    assert cleaned.json()["marked"] >= 1
    row = db.fetchone("SELECT context_skip FROM messages WHERE id = ?", (mid,))
    assert int(row["context_skip"]) == 1
