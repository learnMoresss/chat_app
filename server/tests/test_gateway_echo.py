"""端到端：worker-agent 启 echo → 网关建任务 → 拿到回声结果。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "worker-agent"
SERVER_DIR = ROOT / "server"


@pytest.fixture(scope="module")
def agent_proc():
    agent_py = AGENT_DIR / ".venv" / "bin" / "python"
    if not agent_py.is_file():
        pytest.skip("worker-agent .venv 未安装")
    env = os.environ.copy()
    env["WORKER_TOKEN"] = "dev-worker-token"
    env["WORKER_HOST"] = "127.0.0.1"
    env["WORKER_PORT"] = "9108"
    env["PLUGINS_ROOT"] = str(ROOT / "plugins")
    proc = subprocess.Popen(
        [str(agent_py), "-m", "agent"],
        cwd=str(AGENT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    with httpx.Client(timeout=2.0) as c:
        while time.time() < deadline:
            try:
                r = c.get("http://127.0.0.1:9108/health")
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            proc.kill()
            pytest.fail("worker-agent 未起来")
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_agent_lists_echo(agent_proc):
    with httpx.Client(timeout=5.0) as c:
        r = c.get(
            "http://127.0.0.1:9108/v1/plugins",
            headers={"Authorization": "Bearer dev-worker-token"},
        )
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()["plugins"]]
        assert "echo" in ids


def test_agent_start_echo_job(agent_proc):
    headers = {"Authorization": "Bearer dev-worker-token"}
    with httpx.Client(timeout=30.0) as c:
        r = c.post(
            "http://127.0.0.1:9108/v1/instances/start",
            headers=headers,
            json={"plugin_id": "echo"},
        )
        assert r.status_code == 200, r.text
        inst = r.json()
        assert inst["state"] == "ready"
        base = inst["base_url"]
        token = inst["token"]
        jr = c.post(
            f"{base}/jobs",
            headers={"Authorization": f"Bearer {token}"},
            json={"capability": "echo", "input": {"text": "你好"}},
        )
        assert jr.status_code == 200, jr.text
        job_id = jr.json()["job_id"]
        deadline = time.time() + 5
        result = None
        while time.time() < deadline:
            gr = c.get(
                f"{base}/jobs/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            body = gr.json()
            if body.get("status") == "done":
                result = body.get("result")
                break
            time.sleep(0.05)
        assert result == "[echo] 你好"
        c.post(
            f"http://127.0.0.1:9108/v1/instances/{inst['instance_id']}/stop",
            headers=headers,
        )


def test_gateway_create_job_via_api(agent_proc, tmp_path, monkeypatch):
    # 独立 DATA_DIR，避免污染开发库
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "test-secret-gateway")
    monkeypatch.setenv("GATEWAY_CONFIG", str(SERVER_DIR / "gateway.yaml"))
    # 清 settings 缓存
    from app.config import get_settings
    from app import db
    from app.main import create_app
    from app.security import hash_password, create_access_token, new_id, iso

    get_settings.cache_clear()
    db.reset_connection()

    app = create_app()
    from fastapi.testclient import TestClient

    client = TestClient(app)
    # 注册管理员
    uid = new_id("u_")
    did = new_id("d_")
    db.connect()
    db.execute(
        """
        INSERT INTO users (id, username, password_hash, display_name, is_admin, is_disabled, created_at)
        VALUES (?, 'gwadmin', ?, 'gw', 1, 0, ?)
        """,
        (uid, hash_password("password1"), iso()),
    )
    db.execute(
        """
        INSERT INTO devices (id, user_id, device_name, refresh_token_hash, created_at, last_active_at)
        VALUES (?, ?, 'test', 'x', ?, ?)
        """,
        (did, uid, iso(), iso()),
    )
    from app.gateway.registry import sync_workers_from_config

    sync_workers_from_config()
    token = create_access_token(user_id=uid, device_id=did)

    r = client.post(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {token}"},
        json={"capability": "echo", "plugin_id": "echo", "input": {"text": "集群"}},
    )
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] in ("running", "done", "queued")
    job_id = job["id"]
    deadline = time.time() + 10
    final = None
    while time.time() < deadline:
        gr = client.get(
            f"/api/v1/jobs/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert gr.status_code == 200
        final = gr.json()
        if final["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert final is not None
    assert final["status"] == "done"
    res = final["result"]
    assert isinstance(res, dict)
    assert res.get("text") == "[echo] 集群"
