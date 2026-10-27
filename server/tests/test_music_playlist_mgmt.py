"""曲库歌单：加曲 / 移除 / 删曲。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "music-mgmt")
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
        json={"username": "mu", "password": "pass1234", "device_name": "t"},
    )
    assert reg.status_code == 200
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}, reg.json()["user"]["id"]


def test_playlist_remove_and_delete_track(client: TestClient, tmp_path: Path):
    from app import db
    from app.security import iso, new_id

    h, uid = _auth(client)
    tid = new_id("mt_")
    now = iso()
    fake = tmp_path / "a.mp3"
    fake.write_bytes(b"ID3fake")
    db.execute(
        """
        INSERT INTO music_tracks(id, user_id, source, title, artist, path, rel_path,
          duration_sec, root_label, meta_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (tid, uid, "cache", "测歌", "艺人", str(fake), "a.mp3", 10, "cache", "{}", now, now),
    )
    pl = client.post(
        "/api/v1/music/playlists",
        headers=h,
        json={"name": "试听", "kind": "library"},
    )
    assert pl.status_code == 200, pl.text
    pid = pl.json()["id"]
    add = client.post(
        f"/api/v1/music/playlists/{pid}/items",
        headers=h,
        json={"track_id": tid},
    )
    assert add.status_code == 200, add.text
    iid = add.json()["id"]
    items = client.get(f"/api/v1/music/playlists/{pid}/items", headers=h)
    assert items.status_code == 200
    assert len(items.json()["items"]) == 1
    rm = client.delete(f"/api/v1/music/playlists/{pid}/items/{iid}", headers=h)
    assert rm.status_code == 200
    items2 = client.get(f"/api/v1/music/playlists/{pid}/items", headers=h).json()["items"]
    assert items2 == []
    # 再加后删曲目（连带清歌单引用）
    client.post(f"/api/v1/music/playlists/{pid}/items", headers=h, json={"track_id": tid})
    dt = client.delete(f"/api/v1/music/tracks/{tid}", headers=h)
    assert dt.status_code == 200
    assert client.get(f"/api/v1/music/playlists/{pid}/items", headers=h).json()["items"] == []
