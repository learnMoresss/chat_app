"""人设橡皮擦 CRUD。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "eraser-secret")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def test_eraser_crud(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "eruser", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    sync = client.get("/api/v1/sync", headers=h).json()
    pid = sync["personas"][0]["id"]

    created = client.post(
        f"/api/v1/personas/{pid}/erasers",
        headers=h,
        json={"pattern": "程序格式", "mode": "sentence", "enabled": True},
    )
    assert created.status_code == 200, created.text
    eid = created.json()["id"]
    assert created.json()["hits"] == 0

    listed = client.get(f"/api/v1/personas/{pid}/erasers", headers=h)
    assert listed.status_code == 200
    assert any(x["id"] == eid for x in listed.json())

    patched = client.patch(
        f"/api/v1/personas/{pid}/erasers/{eid}",
        headers=h,
        json={"enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    deleted = client.delete(f"/api/v1/personas/{pid}/erasers/{eid}", headers=h)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/personas/{pid}/erasers", headers=h).json() == []
