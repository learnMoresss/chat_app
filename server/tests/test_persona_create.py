"""新建人设：名字 / bio / 颜色 / 渠道，并自动开会话。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "persona-create-secret")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def test_create_and_patch_persona(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "puser", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/api/v1/personas",
        headers=h,
        json={
            "name": "阿狸",
            "bio": "短句、俏皮",
            "color": 0xFFE74C3C,
            "channel_id": "",
            "greet": True,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["name"] == "阿狸"
    assert body["bio"] == "短句、俏皮"
    assert body["color"] == 0xFFE74C3C
    assert body["state"] == "friend"
    pid = body["id"]

    sync = client.get("/api/v1/sync", headers=h)
    assert sync.status_code == 200
    convs = sync.json()["conversations"]
    assert any(c.get("persona_id") == pid for c in convs)

    patched = client.patch(
        f"/api/v1/personas/{pid}",
        headers=h,
        json={"name": "阿狸狸", "color": 0xFF2F6BFF, "bio": "更温柔一点"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "阿狸狸"
    assert patched.json()["color"] == 0xFF2F6BFF
    assert patched.json()["bio"] == "更温柔一点"
