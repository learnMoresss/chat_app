"""不依赖外网：mock yt-dlp 验证 download 插件 probe 路径。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "plugins" / "download" / "main.py"


@pytest.fixture(scope="module")
def dl():
    spec = importlib.util.spec_from_file_location("dl_main", MOD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dl_main"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_probe_mocked(dl, monkeypatch):
    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {
                "title": "demo",
                "duration": 12,
                "thumbnail": "http://example.com/t.jpg",
                "extractor": "generic",
                "webpage_url": url,
                "ext": "mp4",
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    # 重新绑定模块内 import —— _probe 内部 import yt_dlp
    info = dl._probe("https://example.com/a.mp4")
    assert info["title"] == "demo"
    assert info["duration"] == 12
