"""借鉴 qq-bridge：出站撕草稿 + 未唤醒缓冲。"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_strip_drafts():
    from app.outbound_gate import strip_drafts

    vis, draft = strip_drafts("你好呀（（其实有点想他））今晚吃啥")
    assert "其实有点想他" in draft
    assert "（（" not in vis
    assert "你好呀" in vis and "今晚吃啥" in vis

    vis2, _ = strip_drafts("ok ((secret)) end")
    assert "secret" not in vis2
    assert "ok" in vis2 and "end" in vis2


@pytest.fixture()
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("SECRET_KEY", "test-qq-bridge-borrow")
    from app import config, db

    config.get_settings.cache_clear()
    db.reset_connection()
    db.connect()
    yield
    db.reset_connection()
    config.get_settings.cache_clear()


def test_wake_buffer_record_and_consume(db_env):
    from app.wake_buffer import buffer_count, consume_summary, record_skipped

    uid, cid = "u_test", "c_test"
    record_skipped(uid, cid, text="第一条闲聊", reason="quiet")
    record_skipped(uid, cid, text="第二条", reason="quiet")
    assert buffer_count(uid, cid) == 2
    block = consume_summary(uid, cid, limit=8)
    assert "未回复期间" in block
    assert "第一条闲聊" in block
    assert buffer_count(uid, cid) == 0
    assert consume_summary(uid, cid) == ""
