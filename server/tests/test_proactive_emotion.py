"""情绪提示、自动配方、媒体意图与氛围卡。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "emo-secret")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def test_extract_media_intents():
    from app.media_cards import extract_media_intents

    text, intents = extract_media_intents("想你了\n⟦图：窗边的猫⟧\n再聊")
    assert "窗边" not in text or "⟦" not in text
    assert "想你了" in text
    assert any(i["kind"] == "image" and "猫" in i["prompt"] for i in intents)

    text2, intents2 = extract_media_intents("看看这个 [[视频：海边日落]] ⟦表情：开心⟧")
    assert all(i["kind"] != "video" for i in intents2)
    assert any(i["kind"] == "sticker" for i in intents2)


def test_seed_auto_schedules(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "emou", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    sync = client.get("/api/v1/sync", headers=h).json()
    pid = sync["personas"][0]["id"]
    listed = client.get("/api/v1/schedules", headers=h, params={"persona_id": pid})
    assert listed.status_code == 200
    kinds = {s["kind"] for s in listed.json()["schedules"]}
    assert "greet_sleep" in kinds
    assert "greet_morning" in kinds
    assert "moment_daily" in kinds


def test_toggle_greet_pauses(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "emou2", "password": "pass1234", "device_name": "t"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    sync = client.get("/api/v1/sync", headers=h).json()
    pid = sync["personas"][0]["id"]
    patched = client.patch(
        f"/api/v1/personas/{pid}",
        headers=h,
        json={"greet": False, "moments_on": False},
    )
    assert patched.status_code == 200
    listed = client.get("/api/v1/schedules", headers=h, params={"persona_id": pid}).json()
    for s in listed["schedules"]:
        if s["kind"].startswith("greet_") or s["kind"].startswith("moment_"):
            assert s.get("paused") in (True, 1)


def test_mood_card_and_moment(client: TestClient):
    from unittest.mock import patch

    from app.media_cards import make_mood_card
    from app.routers.moments import post_persona_moment

    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "emou3", "password": "pass1234", "device_name": "t"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    uid = reg.json()["user"]["id"]
    sync = client.get("/api/v1/sync", headers=h).json()
    pid = sync["personas"][0]["id"]

    fid = make_mood_card(user_id=uid, caption="夜色很好", persona_name="深深")
    assert fid
    with patch("app.media_cards.generate_or_mood", return_value=fid):
        mid = post_persona_moment(
            user_id=uid,
            persona_id=pid,
            text="风有点凉\n⟦图：河边的风⟧",
        )
    assert mid
    listed = client.get("/api/v1/moments", headers=h).json()["moments"]
    hit = next(m for m in listed if m["id"] == mid)
    assert "风" in hit["text"]
    assert "⟦" not in hit["text"]
    assert hit["media"]  # 至少一张示意卡


def test_emotion_in_system_prompt():
    from app.llm import build_messages

    msgs = build_messages(
        persona_bio="软软的，会撒娇",
        persona_name="深深",
        history=[{"role": "user", "content": "在吗"}],
    )
    sys = msgs[0]["content"]
    assert "情绪" in sys
    assert "私聊" in sys or "接话" in sys
    assert "emoji" in sys.lower() or "颜文字" in sys
    assert "⟦表情" in sys
    assert "接话" in msgs[-1]["content"]
