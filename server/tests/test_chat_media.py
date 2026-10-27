"""聊天媒体上传 + 带 media_ids 发图片消息。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "chat-media-secret")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def test_upload_and_send_images(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "mediau", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    sync = client.get("/api/v1/sync", headers=h).json()
    convs = sync["conversations"]
    assert convs, "注册应有深深会话"
    conv_id = convs[0]["id"]

    ids = []
    for i in range(2):
        r = client.post(
            "/api/v1/media/chat-upload",
            headers=h,
            files={"file": (f"a{i}.jpg", b"\xff\xd8\xfffakejpg" + bytes([i]), "image/jpeg")},
            data={"kind": "image"},
        )
        assert r.status_code == 200, r.text
        ids.append(r.json()["file_id"])

    sent = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        headers=h,
        json={
            "client_id": "client-media-001",
            "text": "看看图",
            "kind": "image",
            "media_ids": ids,
        },
    )
    assert sent.status_code == 200, sent.text
    msg = sent.json()["message"]
    assert msg["kind"] == "image"
    assert ids[0] in msg["text"]

    got = client.get(f"/api/v1/media/files/{ids[0]}", headers=h)
    assert got.status_code == 200
    assert got.content.startswith(b"\xff\xd8\xff")
