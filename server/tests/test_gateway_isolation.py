"""网关任务 / 媒体：用户 A 不能读用户 B 的 job 与签名链接。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "worker-agent"
SERVER_DIR = ROOT / "server"


@pytest.fixture(scope="module")
def agent_proc():
    agent_py = AGENT_DIR / ".venv" / "bin" / "python"
    if not agent_py.is_file():
        pytest.skip("worker-agent .venv 未安装")
    env = os.environ.copy()
    env.update(
        {
            "WORKER_TOKEN": "dev-worker-token",
            "WORKER_HOST": "127.0.0.1",
            "WORKER_PORT": "9109",
            "PLUGINS_ROOT": str(ROOT / "plugins"),
        }
    )
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
                if c.get("http://127.0.0.1:9109/health").status_code == 200:
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


@pytest.fixture()
def two_users(tmp_path, monkeypatch, agent_proc):
    # 临时 gateway 指向 9109
    gw = tmp_path / "gateway.yaml"
    gw.write_text(
        """
workers:
  - id: local
    name: test
    base_url: http://127.0.0.1:9109
    token: dev-worker-token
    enabled: true
grants:
  - capability: echo
    mode: all
    daily_limit: 100
""",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "iso-gateway-secret")
    monkeypatch.setenv("GATEWAY_CONFIG", str(gw))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")

    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        a = client.post(
            "/api/v1/auth/register",
            json={"username": "iso_a", "password": "pass1234", "device_name": "t"},
        ).json()
        b = client.post(
            "/api/v1/auth/register",
            json={"username": "iso_b", "password": "pass1234", "device_name": "t"},
        ).json()
        yield client, a["access_token"], b["access_token"]
    db.reset_connection()
    config.get_settings.cache_clear()


def test_job_isolation(two_users):
    client, ta, tb = two_users
    r = client.post(
        "/api/v1/jobs",
        headers={"Authorization": f"Bearer {ta}"},
        json={"capability": "echo", "plugin_id": "echo", "input": {"text": "secret"}},
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    # B 读 A 的任务
    denied = client.get(
        f"/api/v1/jobs/{job_id}",
        headers={"Authorization": f"Bearer {tb}"},
    )
    assert denied.status_code == 404
    # A 自己能读
    ok = client.get(
        f"/api/v1/jobs/{job_id}",
        headers={"Authorization": f"Bearer {ta}"},
    )
    assert ok.status_code == 200


def test_media_signed_url_isolation(two_users):
    client, ta, tb = two_users
    from urllib.parse import parse_qs, urlparse

    from app import db
    from app.config import get_settings
    from app.gateway.signed_urls import make_download_url
    from app.security import iso, new_id

    ua = db.fetchone("SELECT id FROM users WHERE username = ?", ("iso_a",))
    ub = db.fetchone("SELECT id FROM users WHERE username = ?", ("iso_b",))
    assert ua and ub
    fid = new_id("mf_")
    fpath = get_settings().data_dir / "files" / ua["id"] / fid
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_bytes(b"alice-secret")
    db.execute(
        """
        INSERT INTO media_files (id, user_id, path, content_type, size, sha256, source, created_at)
        VALUES (?, ?, ?, 'application/octet-stream', ?, '', 'upload', ?)
        """,
        (fid, ua["id"], str(fpath), fpath.stat().st_size, iso()),
    )
    url_a = make_download_url(file_id=fid, user_id=ua["id"])
    qs = parse_qs(urlparse(url_a).query)
    bad = client.get(
        "/api/v1/media/download",
        params={
            "file_id": fid,
            "user_id": ub["id"],
            "exp": qs["exp"][0],
            "sig": qs["sig"][0],
        },
    )
    assert bad.status_code == 403
    good = client.get(
        "/api/v1/media/download",
        params={
            "file_id": fid,
            "user_id": ua["id"],
            "exp": qs["exp"][0],
            "sig": qs["sig"][0],
        },
    )
    assert good.status_code == 200
    assert good.content == b"alice-secret"
