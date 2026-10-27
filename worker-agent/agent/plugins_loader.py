from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .config import get_settings

ALLOWED_PLACEHOLDERS = {"port", "token", "cuda_visible_devices", "plugin_id", "python"}


def plugins_root() -> Path:
    root = get_settings().plugins_root
    if not root.is_absolute():
        # 相对 worker-agent 目录
        return (Path(__file__).resolve().parent.parent / root).resolve()
    return root.resolve()


def load_all() -> Dict[str, Dict[str, Any]]:
    root = plugins_root()
    out: Dict[str, Dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.iterdir()):
        yaml_path = path / "plugin.yaml"
        if not yaml_path.is_file():
            continue
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        pid = str(data.get("id") or path.name)
        if data.get("runtime", "process") != "process":
            # 第一版只登记 process；其它 runtime 可见但不启动
            data["_startable"] = False
        else:
            data["_startable"] = True
        data["_dir"] = str(path.resolve())
        data["id"] = pid
        _validate_command(data)
        out[pid] = data
    return out


def get_plugin(plugin_id: str) -> Optional[Dict[str, Any]]:
    return load_all().get(plugin_id)


def _validate_command(data: Dict[str, Any]) -> None:
    cmd = data.get("command")
    if not isinstance(cmd, list) or not cmd:
        raise ValueError(f"plugin {data.get('id')}: command 必须是非空列表")
    for part in cmd:
        if not isinstance(part, str):
            raise ValueError(f"plugin {data.get('id')}: command 项必须是字符串")
        # 只允许 {name} 形式的占位符
        if "{" in part:
            name = part.strip("{}")
            if part != "{" + name + "}" or name not in ALLOWED_PLACEHOLDERS:
                # 允许字符串里嵌入 {port} 等：简单扫描
                import re

                for m in re.findall(r"\{([a-z_]+)\}", part):
                    if m not in ALLOWED_PLACEHOLDERS:
                        raise ValueError(
                            f"plugin {data.get('id')}: 不允许的占位符 {{{m}}}"
                        )


def list_public() -> List[Dict[str, Any]]:
    """给 API 用的精简视图。"""
    rows = []
    for p in load_all().values():
        rows.append(
            {
                "id": p["id"],
                "name": p.get("name", p["id"]),
                "version": p.get("version", ""),
                "capabilities": p.get("capabilities", []),
                "runtime": p.get("runtime", "process"),
                "startable": p.get("_startable", False),
                "vram_mb": int(p.get("vram_mb") or 0),
                "max_concurrency": int(p.get("max_concurrency") or 1),
                "idle_tier": p.get("idle_tier", "light"),
                "cold_start_seconds": int(p.get("cold_start_seconds") or 0),
            }
        )
    return rows
