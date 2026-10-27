"""主动问候：配方、跳过条件、排班。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "sched-secret")
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
        json={"username": "schuser", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    sync = client.get("/api/v1/sync", headers=h).json()
    pid = sync["personas"][0]["id"]
    return h, pid, sync["me"]["id"]


def test_install_recipe_and_list(client: TestClient):
    h, pid, _ = _auth(client)
    r = client.post(
        "/api/v1/schedules/recipes",
        headers=h,
        json={"persona_id": pid, "recipe": "greet_sleep"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "greet_sleep"
    assert body["next_run_at"]
    listed = client.get("/api/v1/schedules", headers=h, params={"persona_id": pid})
    assert listed.status_code == 200
    assert any(s["id"] == body["id"] for s in listed.json()["schedules"])


def test_skip_stop_chase_and_fire_ok(client: TestClient):
    from app import db, schedules as sched
    from app.security import iso, new_id

    h, pid, uid = _auth(client)
    installed = client.post(
        "/api/v1/schedules/recipes",
        headers=h,
        json={"persona_id": pid, "recipe": "greet_morning"},
    ).json()
    sync = client.get("/api/v1/sync", headers=h).json()
    conv_id = next(c["id"] for c in sync["conversations"] if c.get("persona_id") == pid)

    # 用户说去睡了
    db.execute(
        """
        INSERT INTO messages(id, client_id, seq, user_id, conv_id, sender_id, mine, kind, text, state, created_at, updated_at)
        VALUES (?, ?, 50, ?, ?, ?, 1, 'text', '我去睡了', 'sent', ?, ?)
        """,
        (new_id("m_"), new_id("c_"), uid, conv_id, uid, iso(), iso()),
    )
    row = db.fetchone("SELECT * FROM schedules WHERE id = ?", (installed["id"],))
    reason = sched.evaluate_skip(row, conv_id=conv_id)
    assert reason is not None
    assert "睡" in reason or "忙" in reason or "追" in reason

    # 清掉停追后再立即跑
    db.execute("DELETE FROM messages WHERE conv_id = ? AND text LIKE '%睡%'", (conv_id,))
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    db.execute("UPDATE schedules SET next_run_at = ? WHERE id = ?", (past, installed["id"]))

    with patch("app.routers.chat._generate_reply", new_callable=AsyncMock) as mocked:
        import asyncio

        row2 = db.fetchone("SELECT * FROM schedules WHERE id = ?", (installed["id"],))
        status = asyncio.run(sched.fire_schedule(dict(row2)))
        assert status == "ok"
        mocked.assert_awaited()
        assert mocked.await_args.kwargs.get("internal") is True


def test_compute_next_run_future():
    from app.schedules import compute_next_run

    nxt = compute_next_run(hour=23, minute=30, jitter_minutes=0)
    assert datetime.fromisoformat(nxt) > datetime.now(timezone.utc)
