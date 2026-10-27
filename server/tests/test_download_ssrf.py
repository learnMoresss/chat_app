"""download 插件 SSRF 规则：不发起真实下载。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = ROOT / "plugins" / "download" / "main.py"


def _load():
    spec = importlib.util.spec_from_file_location("plugin_download_main", MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # 避免执行 uvicorn 主流程；模块顶层只定义函数
    sys.modules["plugin_download_main"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def refuse():
    mod = _load()
    return mod._refuse_ssrf


def test_refuse_localhost(refuse):
    with pytest.raises(ValueError):
        refuse("http://127.0.0.1/x")
    with pytest.raises(ValueError):
        refuse("http://localhost/x")


def test_refuse_file_scheme(refuse):
    with pytest.raises(ValueError):
        refuse("file:///etc/passwd")


def test_allow_https_public_shape(refuse):
    # 只检查协议与解析形态；若 DNS 把 example.com 指到内网会拒（少见）
    refuse("https://example.com/watch?v=1")
