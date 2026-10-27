"""M2/M5：证据注入、高危策略、无 KVM 安卓。"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "test-evidence-policy")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    db.connect()
    yield
    db.reset_connection()
    config.get_settings.cache_clear()


def _seed_user(uid: str = "u1", pid: str = "p1") -> None:
    from app import db

    db.execute(
        "INSERT INTO users(id, username, display_name, password_hash, created_at) VALUES(?,?,?,?,?)",
        (uid, uid, uid, "x", "2020-01-01T00:00:00Z"),
    )
    db.execute(
        """
        INSERT INTO personas(id, user_id, name, bio, channel_id, created_at)
        VALUES (?, ?, 't', '', '', ?)
        """,
        (pid, uid, "2020-01-01T00:00:00Z"),
    )


def test_build_evidence_budget(db_env):
    from app import memory as mem
    from app.evidence import add_person_note, build_evidence

    uid, pid = "u1", "p1"
    _seed_user(uid, pid)
    add_person_note(uid, pid, "对方叫小明")
    for i in range(10):
        mem.add_memory(uid, pid, f"记忆条目编号{i}内容够长一些")
    block, kept = build_evidence(uid, pid, "小明", conv_id="c1", enabled=True)
    assert "本轮证据" in block
    assert 1 <= len(kept) <= 8
    assert len(block) <= 700
    empty, none = build_evidence(uid, pid, "x", enabled=False)
    assert empty == "" and none == []


def test_capability_fail_closed(db_env):
    from app.capability_policy import assert_allowed, is_dangerous

    assert is_dangerous("shell_exec")
    with pytest.raises(PermissionError):
        assert_allowed("shell_exec")
    from app import db

    db.execute(
        "INSERT INTO server_settings(key, value) VALUES(?, ?)",
        ("capability_allow_dangerous", "shell_exec"),
    )
    assert_allowed("shell_exec")


def test_android_no_kvm(db_env, monkeypatch):
    import os
    import shutil

    monkeypatch.setattr(os.path, "exists", lambda p: False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    _seed_user("u_android", "p_android")
    from app.virtual_devices import create_device

    d = create_device("u_android", kind="android", label="phone")
    assert d["status"] == "unavailable"
    meta = d.get("meta") or {}
    assert meta.get("reason") == "no_kvm" or "no_kvm" in str(d)
