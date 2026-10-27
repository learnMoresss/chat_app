"""渠道自检：无 Key 时明确失败。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "probe-secret")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def test_probe_without_key(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "probeu", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200
    token = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    ch = client.post(
        "/api/v1/channels",
        headers=h,
        json={
            "name": "local",
            "model": "x",
            "base_url": "http://127.0.0.1:9",
            "api_key": "",
        },
    )
    assert ch.status_code == 200, ch.text
    cid = ch.json()["id"]
    # 清空 key：再 patch 空字符串
    client.patch(
        f"/api/v1/channels/{cid}",
        headers=h,
        json={"name": "local", "model": "x", "base_url": "http://127.0.0.1:9", "api_key": ""},
    )
    r = client.post(f"/api/v1/channels/{cid}/probe", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "Key" in body["error"] or "key" in body["error"].lower() or "未填写" in body["error"]
