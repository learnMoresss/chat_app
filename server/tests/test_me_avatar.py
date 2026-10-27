"""自己的头像与显示名：PATCH /auth/me + sync.me。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "me-avatar-secret")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def test_patch_me_display_and_avatar(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "meuser", "password": "pass1234", "device_name": "t", "display_name": "旧名"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200
    assert me.json()["display_name"] == "旧名"
    assert me.json().get("avatar_file_id", "") == ""

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    up = client.post(
        "/api/v1/media/chat-upload",
        headers=h,
        files={"file": ("avatar.png", png, "image/png")},
        data={"kind": "image"},
    )
    assert up.status_code == 200, up.text
    fid = up.json()["file_id"]

    patched = client.patch(
        "/api/v1/auth/me",
        headers=h,
        json={"display_name": "新名", "avatar_file_id": fid},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["display_name"] == "新名"
    assert patched.json()["avatar_file_id"] == fid

    sync = client.get("/api/v1/sync", headers=h)
    assert sync.status_code == 200
    assert sync.json()["me"]["display_name"] == "新名"
    assert sync.json()["me"]["avatar_file_id"] == fid
