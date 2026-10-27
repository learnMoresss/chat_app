"""外链分享：创建 → 匿名下载 → 撤销；任务列表。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "share-test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")

    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
    db.reset_connection()
    config.get_settings.cache_clear()


def test_share_create_download_revoke(client: TestClient):
    from app import db
    from app.config import get_settings
    from app.security import iso, new_id

    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "shareuser", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    uid = reg.json()["user"]["id"]

    settings = get_settings()
    media_dir = Path(settings.data_dir) / "files"
    media_dir.mkdir(parents=True, exist_ok=True)
    fpath = media_dir / "hello.txt"
    fpath.write_text("hello-share", encoding="utf-8")
    fid = new_id("mf_")
    db.execute(
        """
        INSERT INTO media_files (id, user_id, path, content_type, size, sha256, source, created_at)
        VALUES (?, ?, ?, 'text/plain', ?, '', 'upload', ?)
        """,
        (fid, uid, str(fpath), fpath.stat().st_size, iso()),
    )

    r = client.post(
        "/api/v1/shares",
        headers={"Authorization": f"Bearer {token}"},
        json={"file_id": fid, "expires_hours": 1, "max_downloads": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    path = body["url"].replace("http://testserver", "")
    assert path.startswith("/api/v1/shares/public/")

    d = client.get(path)
    assert d.status_code == 200
    assert d.content == b"hello-share"

    rev = client.delete(
        f"/api/v1/shares/{body['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rev.status_code == 200
    assert client.get(path).status_code == 404


def test_list_jobs_empty(client: TestClient):
    reg = client.post(
        "/api/v1/auth/register",
        json={"username": "joblist", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    r = client.get("/api/v1/jobs", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["jobs"] == []


def test_share_cross_user_revoke(client: TestClient):
    """用户 B 不能撤销用户 A 的分享。"""
    from app import db
    from app.config import get_settings
    from app.security import iso, new_id
    from pathlib import Path

    a = client.post(
        "/api/v1/auth/register",
        json={"username": "sha", "password": "pass1234", "device_name": "t"},
    ).json()
    b = client.post(
        "/api/v1/auth/register",
        json={"username": "shb", "password": "pass1234", "device_name": "t"},
    ).json()
    settings = get_settings()
    fpath = Path(settings.data_dir) / "files" / "x.txt"
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text("x", encoding="utf-8")
    fid = new_id("mf_")
    db.execute(
        """
        INSERT INTO media_files (id, user_id, path, content_type, size, sha256, source, created_at)
        VALUES (?, ?, ?, 'text/plain', 1, '', 'upload', ?)
        """,
        (fid, a["user"]["id"], str(fpath), iso()),
    )
    created = client.post(
        "/api/v1/shares",
        headers={"Authorization": f"Bearer {a['access_token']}"},
        json={"file_id": fid},
    )
    assert created.status_code == 200
    sid = created.json()["id"]
    denied = client.delete(
        f"/api/v1/shares/{sid}",
        headers={"Authorization": f"Bearer {b['access_token']}"},
    )
    assert denied.status_code == 404
