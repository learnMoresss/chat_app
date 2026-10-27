from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .. import db
from ..config import get_settings
from ..security import iso, new_id
from .agent_client import AgentClient

_config_cache: Optional[Dict[str, Any]] = None


def gateway_config_path() -> Path:
    settings = get_settings()
    if settings.gateway_config:
        return Path(settings.gateway_config)
    # 相对 server 工作目录
    return Path("gateway.yaml")


def load_gateway_config(*, force: bool = False) -> Dict[str, Any]:
    global _config_cache
    if _config_cache is not None and not force:
        return _config_cache
    path = gateway_config_path()
    if not path.is_file():
        _config_cache = {"workers": [], "grants": [], "remote_instances": []}
        return _config_cache
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _config_cache = {
        "workers": list(data.get("workers") or []),
        "grants": list(data.get("grants") or []),
        "remote_instances": list(data.get("remote_instances") or []),
    }
    return _config_cache


def sync_workers_from_config() -> None:
    """把 YAML 里的 worker 写入/更新 workers 表。"""
    cfg = load_gateway_config(force=True)
    now = iso()
    for w in cfg["workers"]:
        wid = str(w["id"])
        row = db.fetchone("SELECT id FROM workers WHERE id = ?", (wid,))
        if row:
            db.execute(
                """
                UPDATE workers SET name=?, base_url=?, token=?, enabled=?, updated_at=?
                WHERE id=?
                """,
                (
                    w.get("name") or wid,
                    w["base_url"],
                    w["token"],
                    1 if w.get("enabled", True) else 0,
                    now,
                    wid,
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO workers (id, name, base_url, token, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    wid,
                    w.get("name") or wid,
                    w["base_url"],
                    w["token"],
                    1 if w.get("enabled", True) else 0,
                    now,
                    now,
                ),
            )


def enabled_workers() -> List[Dict[str, Any]]:
    rows = db.fetchall("SELECT * FROM workers WHERE enabled = 1")
    return [dict(r) for r in rows]


def agent_for(worker: Dict[str, Any]) -> AgentClient:
    return AgentClient(worker["base_url"], worker["token"])


def list_capabilities_for_user(*, user_id: str, is_admin: bool) -> List[Dict[str, Any]]:
    """只读：当前用户能用的能力（来自 grants + 已发现插件）。"""
    cfg = load_gateway_config()
    caps: Dict[str, Dict[str, Any]] = {}
    # 从各 worker 拉插件清单（失败则跳过）
    for w in enabled_workers():
        try:
            client = agent_for(w)
            for p in client.list_plugins():
                for c in p.get("capabilities") or []:
                    caps[c] = {
                        "capability": c,
                        "plugin_id": p["id"],
                        "plugin_name": p.get("name"),
                        "cold_start_seconds": p.get("cold_start_seconds", 0),
                        "available": True,
                    }
        except Exception:
            continue
    # grants：worker 在线则带插件信息；离线仍列出授权项（available=false）
    out: List[Dict[str, Any]] = []
    for g in cfg["grants"]:
        cap = g["capability"]
        mode = g.get("mode", "all")
        allowed = False
        if mode == "all":
            allowed = True
        elif mode == "admins":
            allowed = is_admin
        elif mode == "users":
            allowed = user_id in (g.get("user_ids") or [])
        if not allowed:
            continue
        if cap in caps:
            item = dict(caps[cap])
            item["daily_limit"] = g.get("daily_limit")
            item["allowed"] = True
            out.append(item)
        else:
            out.append(
                {
                    "capability": cap,
                    "plugin_id": "",
                    "plugin_name": "",
                    "title": cap,
                    "description": "已在 gateway.yaml 授权，但 worker 未上报该插件（未启动或未安装）",
                    "cold_start_seconds": 0,
                    "available": False,
                    "allowed": True,
                    "daily_limit": g.get("daily_limit"),
                    "offline": True,
                }
            )
    return out


def user_may_use(capability: str, *, user_id: str, is_admin: bool) -> bool:
    return any(
        c["capability"] == capability
        for c in list_capabilities_for_user(user_id=user_id, is_admin=is_admin)
    )
