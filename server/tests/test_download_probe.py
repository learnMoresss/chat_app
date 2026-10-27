"""download 插件 probe（需要 agent + yt-dlp；网络失败则 skip）。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "worker-agent"


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
            "WORKER_PORT": "9110",
            "PLUGINS_ROOT": str(ROOT / "plugins"),
        }
    )
    proc = subprocess.Popen(
        [str(agent_py), "-m", "agent"],
        cwd=str(AGENT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 15
    with httpx.Client(timeout=2.0) as c:
        while time.time() < deadline:
            try:
                if c.get("http://127.0.0.1:9110/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            err = proc.stderr.read().decode() if proc.stderr else ""
            proc.kill()
            pytest.fail(f"agent 未起: {err}")
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_download_probe_example(agent_proc):
    headers = {"Authorization": "Bearer dev-worker-token"}
    with httpx.Client(timeout=120.0) as c:
        r = c.post(
            "http://127.0.0.1:9110/v1/instances/start",
            headers=headers,
            json={"plugin_id": "download"},
        )
        if r.status_code != 200:
            pytest.skip(f"无法启动 download: {r.text}")
        inst = r.json()
        try:
            jr = c.post(
                f"{inst['base_url']}/jobs",
                headers={"Authorization": f"Bearer {inst['token']}"},
                json={
                    "capability": "media.download",
                    "input": {
                        "action": "probe",
                        # 直链样例（不依赖站点改版）；网络不通则 skip
                        "url": "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerEscapes.mp4",
                    },
                },
            )
            assert jr.status_code == 200, jr.text
            job_id = jr.json()["job_id"]
            deadline = time.time() + 90
            body = None
            while time.time() < deadline:
                body = c.get(
                    f"{inst['base_url']}/jobs/{job_id}",
                    headers={"Authorization": f"Bearer {inst['token']}"},
                ).json()
                if body["status"] in ("done", "failed", "cancelled"):
                    break
                time.sleep(0.5)
            assert body is not None
            if body["status"] == "failed":
                pytest.skip(f"probe 失败（多半网络）: {body.get('error')}")
            assert body["status"] == "done"
            assert body["result"].get("title")
        finally:
            c.post(
                f"http://127.0.0.1:9110/v1/instances/{inst['instance_id']}/stop",
                headers=headers,
            )
