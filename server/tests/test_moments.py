"""朋友圈：发/赞/评/删 + 角色自发。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "mom-secret")
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
        json={"username": "momuser", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    sync = client.get("/api/v1/sync", headers=h).json()
    return h, sync["me"]["id"], sync["personas"][0]["id"]


def test_user_moment_crud(client: TestClient):
    h, uid, _ = _auth(client)
    created = client.post(
        "/api/v1/moments",
        headers=h,
        json={"text": "今天风很大"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["text"] == "今天风很大"
    assert body["author_id"] == uid
    mid = body["id"]

    listed = client.get("/api/v1/moments", headers=h)
    assert listed.status_code == 200
    assert any(m["id"] == mid for m in listed.json()["moments"])

    liked = client.post(f"/api/v1/moments/{mid}/like", headers=h)
    assert liked.status_code == 200
    assert liked.json()["liked"] is True
    unliked = client.post(f"/api/v1/moments/{mid}/like", headers=h)
    assert unliked.json()["liked"] is False

    cmt = client.post(
        f"/api/v1/moments/{mid}/comments",
        headers=h,
        json={"text": "记下了"},
    )
    assert cmt.status_code == 200
    assert cmt.json()["text"] == "记下了"

    deleted = client.delete(f"/api/v1/moments/{mid}", headers=h)
    assert deleted.status_code == 200
    listed2 = client.get("/api/v1/moments", headers=h).json()["moments"]
    assert all(m["id"] != mid for m in listed2)


def test_persona_moment_helper(client: TestClient):
    h, uid, pid = _auth(client)
    from app.routers.moments import post_persona_moment
    from unittest.mock import patch

    with patch("app.media_cards.generate_or_mood", return_value=None):
        mid = post_persona_moment(user_id=uid, persona_id=pid, text="路过河边。")
    assert mid
    listed = client.get("/api/v1/moments", headers=h).json()["moments"]
    hit = next(m for m in listed if m["id"] == mid)
    assert hit["author_kind"] == "persona"
    assert hit["author_id"] == pid
    assert "河边" in hit["text"]

    mems = client.get(f"/api/v1/personas/{pid}/memories", headers=h).json()
    assert isinstance(mems, list)
    texts = [m["text"] for m in mems]
    assert any("朋友圈" in t and "河边" in t for t in texts)


def test_persona_moment_with_image_marker(client: TestClient):
    h, uid, pid = _auth(client)
    from app.routers.moments import post_persona_moment
    from unittest.mock import patch

    with patch("app.media_cards.generate_or_mood", return_value="mf_fake_img"):
        mid = post_persona_moment(
            user_id=uid,
            persona_id=pid,
            text="风有点凉\n⟦图：河边的风⟧",
        )
    assert mid
    hit = next(m for m in client.get("/api/v1/moments", headers=h).json()["moments"] if m["id"] == mid)
    assert "⟦" not in hit["text"]
    assert hit["media"] == ["mf_fake_img"]
