"""跨用户隔离：A 拿 B 的资源 id 去读/改/删，应 404。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-isolation")
    # 清缓存后再导入
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()

    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c

    db.reset_connection()
    config.get_settings.cache_clear()


def _register(client: TestClient, username: str, password: str = "pass1234") -> dict:
    r = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password, "device_name": "test"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_persona_memory_channel_conv_isolation(client: TestClient):
    a = _register(client, "alice_iso")
    b = _register(client, "bob_iso")
    ta, tb = a["access_token"], b["access_token"]

    # A 的人设
    pa = client.get("/api/v1/personas", headers=_auth(ta)).json()
    assert pa, "alice 应有默认人设"
    persona_a = pa[0]["id"]

    # A 加一条记忆
    mem = client.post(
        f"/api/v1/personas/{persona_a}/memories",
        headers=_auth(ta),
        json={"text": "alice 的秘密记忆"},
    )
    assert mem.status_code == 200
    mem_id = mem.json()["id"]

    # A 的会话
    sync_a = client.get("/api/v1/sync", headers=_auth(ta)).json()
    conv_a = sync_a["conversations"][0]["id"]

    # A 的私有渠道
    ch = client.post(
        "/api/v1/channels",
        headers=_auth(ta),
        json={
            "name": "alice-key",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-alice",
            "shared": False,
        },
    )
    assert ch.status_code == 200
    channel_a = ch.json()["id"]

    # B 读 A 的人设 / 记忆 / 会话消息 / 渠道详情 → 404
    assert client.get(f"/api/v1/personas/{persona_a}", headers=_auth(tb)).status_code == 404
    assert (
        client.get(f"/api/v1/personas/{persona_a}/memories", headers=_auth(tb)).status_code == 404
    )
    assert (
        client.patch(
            f"/api/v1/personas/{persona_a}",
            headers=_auth(tb),
            json={"bio": "hacked"},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/personas/{persona_a}/memories/{mem_id}",
            headers=_auth(tb),
            json={"text": "stolen"},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/personas/{persona_a}/memories/{mem_id}",
            headers=_auth(tb),
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/conversations/{conv_a}/messages", headers=_auth(tb)).status_code
        == 404
    )
    assert client.get(f"/api/v1/channels/{channel_a}", headers=_auth(tb)).status_code == 404
    assert (
        client.patch(
            f"/api/v1/channels/{channel_a}",
            headers=_auth(tb),
            json={"name": "stolen", "model": "x", "base_url": "https://x", "api_key": "x"},
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/channels/{channel_a}", headers=_auth(tb)).status_code == 404

    # B 自己的列表里看不到 A 的人设 id
    pb = client.get("/api/v1/personas", headers=_auth(tb)).json()
    assert all(p["id"] != persona_a for p in pb)


def test_change_password_and_login(client: TestClient):
    a = _register(client, "pwd_user", "oldpass1")
    token = a["access_token"]
    r = client.post(
        "/api/v1/auth/change-password",
        headers=_auth(token),
        json={"old_password": "oldpass1", "new_password": "newpass2"},
    )
    assert r.status_code == 200, r.text
    bad = client.post(
        "/api/v1/auth/login",
        json={"username": "pwd_user", "password": "oldpass1", "device_name": "t"},
    )
    assert bad.status_code == 401
    ok = client.post(
        "/api/v1/auth/login",
        json={"username": "pwd_user", "password": "newpass2", "device_name": "t"},
    )
    assert ok.status_code == 200


def test_pick_max_tokens():
    from app.llm import pick_max_tokens

    dense = [{"role": "user", "content": "嗯"} for _ in range(8)]
    assert pick_max_tokens(dense, "好") == 400
    quiet = [{"role": "user", "content": "x" * 100}]
    assert pick_max_tokens(quiet, "y" * 200) == 800
