"""表情包 + 媒体意图（不再要视频）。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "stk-secret")
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
        json={"username": "stku", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    sync = client.get("/api/v1/sync", headers=h).json()
    return h, reg.json()["user"]["id"], sync["personas"][0]["id"]


def test_no_video_intent():
    from app.media_cards import extract_media_intents

    text, intents = extract_media_intents("嗨\n⟦视频：海边⟧\n⟦图：猫⟧\n⟦表情：翻白眼⟧")
    kinds = {i["kind"] for i in intents}
    assert "video" not in kinds
    assert "image" in kinds
    assert "sticker" in kinds
    assert "视频" not in text


def test_sticker_collect_and_pick(client: TestClient):
    from app.media_cards import make_mood_card
    from app import stickers as stk

    h, uid, pid = _auth(client)
    fid = make_mood_card(user_id=uid, caption="翻白眼", persona_name="深深")
    assert fid
    r = client.post(
        "/api/v1/stickers/collect",
        headers=h,
        json={
            "file_id": fid,
            "label": "翻白眼",
            "emotion": "翻白眼",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["owner_kind"] == "library"
    listed = client.get("/api/v1/stickers", headers=h)
    assert listed.status_code == 200
    assert any(s["file_id"] == fid for s in listed.json()["stickers"])
    hit = stk.pick_by_emotion(user_id=uid, persona_id=pid, emotion="翻白眼")
    assert hit and hit["emotion"] == "翻白眼"
    listed_ids = {s["file_id"] for s in listed.json()["stickers"]}
    assert fid in listed_ids


def test_account_library_shared_across_personas(client: TestClient):
    """账号全局库：任意 owner 写入后，各人设都能抽到。"""
    from app.media_cards import make_mood_card
    from app import stickers as stk

    h, uid, pid = _auth(client)
    fid = make_mood_card(user_id=uid, caption="用户图", persona_name="me")
    assert fid
    emo = "专属测试情绪xyz"
    stk.add_sticker(
        user_id=uid,
        file_id=fid,
        label=emo,
        emotion=emo,
        owner_kind="user",
        owner_id=uid,
    )
    hit = stk.pick_by_emotion(user_id=uid, persona_id=pid, emotion=emo)
    assert hit and hit["file_id"] == fid


def test_ensure_persona_stickers(client: TestClient):
    from app import stickers as stk

    h, uid, pid = _auth(client)
    n = stk.ensure_persona_stickers(user_id=uid, persona_id=pid, persona_name="深深")
    assert n >= 3
    hit = stk.pick_by_emotion(user_id=uid, persona_id=pid, emotion="开心")
    assert hit and hit["owner_kind"] == "library"


def test_sticker_delete_and_patch(client: TestClient):
    from app.media_cards import make_mood_card

    h, uid, _ = _auth(client)
    fid = make_mood_card(user_id=uid, caption="删测", persona_name="x")
    assert fid
    r = client.post(
        "/api/v1/stickers/collect",
        headers=h,
        json={"file_id": fid, "label": "旧", "emotion": "旧情"},
    )
    assert r.status_code == 200
    sid = r.json()["id"]
    p = client.patch(
        f"/api/v1/stickers/{sid}",
        headers=h,
        json={"label": "新标", "emotion": "新情"},
    )
    assert p.status_code == 200
    assert p.json()["emotion"] == "新情"
    d = client.delete(f"/api/v1/stickers/{sid}", headers=h)
    assert d.status_code == 200
    listed = client.get("/api/v1/stickers", headers=h).json()["stickers"]
    assert all(s["id"] != sid for s in listed)


def test_augment_sticker_from_text(monkeypatch):
    from app import stickers as stk

    intents = stk.maybe_augment_sticker_intents(
        "哼，才不是呢。", [], cooled=False, probability=1.0
    )
    assert any(i["kind"] == "sticker" and i["prompt"] == "嫌弃" for i in intents)
    cooled = stk.maybe_augment_sticker_intents("哼，才不是呢。", [], cooled=True)
    assert cooled == []


def test_generate_or_mood_fallback(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from app.media_cards import generate_or_mood

    h, uid, _ = _auth(client)
    with patch("app.image_gen.generate_image_bytes", return_value=None):
        fid = generate_or_mood(user_id=uid, caption="窗边", persona_name="深深")
    assert fid
