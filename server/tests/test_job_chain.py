"""下载完成 → 自动接力 video.understand。"""
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
    monkeypatch.setenv("SECRET_KEY", "chain-secret")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def test_chain_on_download_done(client: TestClient):
    from app import db
    from app.gateway import jobs as job_mod
    from app.security import iso, new_id

    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "chainu", "password": "pass1234", "device_name": "t"},
    )
    uid = reg.json()["user"]["id"]
    jid = new_id("job_")
    now = iso()
    db.execute(
        """
        INSERT INTO plugin_jobs (
          id, user_id, persona_id, conversation_id, worker_id, instance_id,
          plugin_id, capability, input_json, status, progress_pct, progress_msg,
          result_json, error, created_at, updated_at, message_id
        ) VALUES (?, ?, '', '', '', '', 'download', 'media.download', ?, 'done', 100, '', '{}', '', ?, ?, '')
        """,
        (
            jid,
            uid,
            '{"action":"download","url":"https://ex.ample/v","then_understand":true}',
            now,
            now,
        ),
    )
    job = dict(db.fetchone("SELECT * FROM plugin_jobs WHERE id = ?", (jid,)))
    with patch.object(
        job_mod,
        "create_job",
        return_value={"id": "job_child_1"},
    ) as mocked:
        job_mod._maybe_chain_video_understand(
            job=job,
            result={"file_id": "mf_1", "title": "demo"},
        )
        assert mocked.called
        kwargs = mocked.call_args.kwargs
        assert kwargs["capability"] == "video.understand"
        assert kwargs["plugin_id"] == "video-mage"
        assert kwargs["input_data"]["url"] == "https://ex.ample/v"
        assert kwargs["input_data"]["file_id"] == "mf_1"

    row = dict(db.fetchone("SELECT result_json FROM plugin_jobs WHERE id = ?", (jid,)))
    assert "job_child_1" in (row["result_json"] or "")


def test_no_chain_without_flag(client: TestClient):
    from app import db
    from app.gateway import jobs as job_mod
    from app.security import iso, new_id

    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "chainu2", "password": "pass1234", "device_name": "t"},
    )
    uid = reg.json()["user"]["id"]
    jid = new_id("job_")
    now = iso()
    db.execute(
        """
        INSERT INTO plugin_jobs (
          id, user_id, persona_id, conversation_id, worker_id, instance_id,
          plugin_id, capability, input_json, status, progress_pct, progress_msg,
          result_json, error, created_at, updated_at, message_id
        ) VALUES (?, ?, '', '', '', '', 'download', 'media.download', ?, 'done', 100, '', '{}', '', ?, ?, '')
        """,
        (jid, uid, '{"action":"download","url":"https://ex.ample/v"}', now, now),
    )
    job = dict(db.fetchone("SELECT * FROM plugin_jobs WHERE id = ?", (jid,)))
    with patch.object(job_mod, "create_job") as mocked:
        job_mod._maybe_chain_video_understand(job=job, result={"file_id": "mf_1"})
        assert not mocked.called
