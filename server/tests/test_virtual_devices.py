"""P3：虚拟设备生命周期与控制租约 fencing。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "test-secret-virtual-devices")
    monkeypatch.setenv("DESKTOP_BACKEND", "mock")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def _register(client: TestClient, username: str) -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "pass1234", "device_name": "t"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_create_stop_preserves_record(client: TestClient):
    t = _register(client, "vd_owner")
    r = client.post("/api/v1/virtual-devices", headers=_h(t), json={"label": "desk1"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "running"
    assert d["view_url"]
    assert d["backend"] == "mock"

    stop = client.post(f"/api/v1/virtual-devices/{d['id']}/stop", headers=_h(t))
    assert stop.status_code == 200, stop.text
    assert stop.json()["status"] == "stopped"
    assert stop.json()["id"] == d["id"]  # 停启不误删

    again = client.get(f"/api/v1/virtual-devices/{d['id']}", headers=_h(t))
    assert again.status_code == 200
    assert again.json()["status"] == "stopped"

    start = client.post(f"/api/v1/virtual-devices/{d['id']}/start", headers=_h(t))
    assert start.status_code == 200
    assert start.json()["status"] == "running"


def test_control_fencing(client: TestClient):
    a = _register(client, "vd_a")
    b = _register(client, "vd_b")
    # 各自创建设备；fencing 测同一设备上的双租约需同 owner ——
    # 产品模型：设备归属单用户。用同一用户双 token 模拟双客户端。
    d = client.post("/api/v1/virtual-devices", headers=_h(a), json={"label": "shared"}).json()
    did = d["id"]

    l1 = client.post(
        f"/api/v1/virtual-devices/{did}/leases",
        headers=_h(a),
        json={"role": "control", "ttl_sec": 120},
    )
    assert l1.status_code == 200, l1.text
    lease1 = l1.json()

    # 同用户再要控制权 → 换新 token（旧 token 失效）
    l2 = client.post(
        f"/api/v1/virtual-devices/{did}/leases",
        headers=_h(a),
        json={"role": "control", "ttl_sec": 120},
    )
    assert l2.status_code == 200
    assert l2.json()["token"] != lease1["token"]

    bad = client.post(
        f"/api/v1/virtual-devices/{did}/leases/{lease1['id']}/heartbeat",
        headers=_h(a),
        json={"token": lease1["token"]},
    )
    assert bad.status_code == 403

    ok = client.post(
        f"/api/v1/virtual-devices/{did}/leases/{l2.json()['id']}/heartbeat",
        headers=_h(a),
        json={"token": l2.json()["token"]},
    )
    assert ok.status_code == 200

    # 多路观看可以并存
    v1 = client.post(
        f"/api/v1/virtual-devices/{did}/leases",
        headers=_h(a),
        json={"role": "view"},
    )
    v2 = client.post(
        f"/api/v1/virtual-devices/{did}/leases",
        headers=_h(a),
        json={"role": "view"},
    )
    assert v1.status_code == 200 and v2.status_code == 200
    assert v1.json()["id"] != v2.json()["id"]

    # 有控制租约时停机拒绝
    refuse = client.post(f"/api/v1/virtual-devices/{did}/stop", headers=_h(a))
    assert refuse.status_code == 409

    # force 可停
    forced = client.post(f"/api/v1/virtual-devices/{did}/stop?force=1", headers=_h(a))
    assert forced.status_code == 200
    assert forced.json()["status"] == "stopped"

    # 隔离：B 看不到 A 的设备
    miss = client.get(f"/api/v1/virtual-devices/{did}", headers=_h(b))
    assert miss.status_code == 404


def test_tunnel_info_endpoint(client: TestClient):
    r = client.get("/api/tunnel")
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body
    assert any(p["id"] == "cloudflared" for p in body["providers"])
    info = client.get("/api/info")
    assert info.status_code == 200
    assert "tunnel_provider" in info.json()
