"""增量 sync + 任务消息落库。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "sync-delta-secret")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def test_sync_delta_after_message(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "deltau", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    sync = client.get("/api/v1/sync", headers=h).json()
    conv_id = sync["conversations"][0]["id"]
    cursor = sync["messages"][-1]["created_at"] if sync["messages"] else "1970-01-01T00:00:00"

    sent = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        headers=h,
        json={"client_id": "delta-cli-00123456", "text": "增量测", "kind": "text"},
    )
    assert sent.status_code == 200, sent.text

    delta = client.get("/api/v1/sync/delta", headers=h, params={"after": cursor})
    assert delta.status_code == 200, delta.text
    body = delta.json()
    assert "cursor" in body
    texts = [m.get("text") for m in body["messages"]]
    assert "增量测" in texts


def test_msg_store_job_card():
    from app.msg_store import job_card_text

    kind, text, _, _ = job_card_text(
        capability="media.download",
        input_data={"action": "download", "url": "https://x"},
        status="queued",
    )
    assert kind == "link"
    assert "下载" in text

    kind2, text2, sec, size = job_card_text(
        capability="media.download",
        input_data={"action": "download", "url": "https://x"},
        status="done",
        result={"file_id": "mf_1", "title": "片", "duration": 12, "size": 100},
    )
    assert kind2 == "video"
    assert "mf_1" in text2
    assert sec == 12
    assert size == 100
