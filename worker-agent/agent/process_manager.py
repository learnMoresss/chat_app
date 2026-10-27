from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from . import gpus as gpu_mod
from .config import get_settings
from .plugins_loader import get_plugin


@dataclass
class Instance:
    instance_id: str
    plugin_id: str
    base_url: str
    token: str
    gpu_index: Optional[int]
    pid: int
    port: int
    process: subprocess.Popen = field(repr=False)
    state: str = "starting"
    started_at: float = field(default_factory=time.time)


class ProcessManager:
    """只启动 plugin.yaml 白名单命令；选端口与 GPU。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instances: Dict[str, Instance] = {}
        self._used_ports: set[int] = set()

    def list_instances(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._reap_locked()
            return [self._public(i) for i in self._instances.values()]

    def get(self, instance_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._reap_locked()
            inst = self._instances.get(instance_id)
            return self._public(inst) if inst else None

    def start(
        self,
        plugin_id: str,
        *,
        vram_mb: Optional[int] = None,
        preferred_gpu: Optional[int] = None,
    ) -> Dict[str, Any]:
        plugin = get_plugin(plugin_id)
        if not plugin:
            raise LookupError(f"未知插件: {plugin_id}")
        if not plugin.get("_startable"):
            raise RuntimeError(f"插件 {plugin_id} 当前 runtime 不可启动")

        need = int(vram_mb if vram_mb is not None else plugin.get("vram_mb") or 0)
        gpu_index = gpu_mod.pick_gpu(need, preferred_gpu)
        if need > 0 and gpu_index is None:
            raise RuntimeError("没有足够空闲显存的 GPU")

        port = self._alloc_port()
        token = secrets.token_urlsafe(24)
        cmd = self._render_command(plugin, port=port, token=token, gpu_index=gpu_index)
        workdir = Path(plugin["_dir"])
        env = os.environ.copy()
        for k, v in (plugin.get("env") or {}).items():
            env[str(k)] = self._render_str(
                str(v), port=port, token=token, gpu_index=gpu_index, plugin_id=plugin_id
            )
        if gpu_index is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
        env["PLUGIN_TOKEN"] = token
        env["PLUGIN_PORT"] = str(port)

        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        instance_id = f"{plugin_id}-{secrets.token_hex(3)}"
        # 对外地址：本机回环；多机时由网关用 agent 所在主机替换主机名
        base_url = f"http://127.0.0.1:{port}"
        inst = Instance(
            instance_id=instance_id,
            plugin_id=plugin_id,
            base_url=base_url,
            token=token,
            gpu_index=gpu_index,
            pid=proc.pid,
            port=port,
            process=proc,
            state="starting",
        )
        with self._lock:
            self._instances[instance_id] = inst

        try:
            self._wait_health(inst, plugin)
            inst.state = "ready"
        except Exception:
            self.stop(instance_id)
            raise
        return self._public(inst)

    def unload(self, instance_id: str) -> Dict[str, Any]:
        with self._lock:
            inst = self._instances.get(instance_id)
            if not inst:
                raise LookupError("实例不存在")
            base, token = inst.base_url, inst.token
            plugin = get_plugin(inst.plugin_id) or {}
            path = plugin.get("unload_path") or "/unload"
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    f"{base}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError:
            pass
        with self._lock:
            if instance_id in self._instances:
                self._instances[instance_id].state = "unloading"
        return self.get(instance_id) or {"instance_id": instance_id, "state": "unloading"}

    def stop(self, instance_id: str) -> Dict[str, Any]:
        with self._lock:
            inst = self._instances.pop(instance_id, None)
            if not inst:
                raise LookupError("实例不存在")
            self._used_ports.discard(inst.port)
            proc = inst.process
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
        return {
            "instance_id": instance_id,
            "plugin_id": inst.plugin_id,
            "state": "stopped",
        }

    def _wait_health(self, inst: Instance, plugin: Dict[str, Any]) -> None:
        settings = get_settings()
        path = plugin.get("health_path") or "/health"
        deadline = time.time() + settings.health_timeout_seconds
        headers = {"Authorization": f"Bearer {inst.token}"}
        last_err = ""
        with httpx.Client(timeout=2.0) as client:
            while time.time() < deadline:
                if inst.process.poll() is not None:
                    raise RuntimeError("插件进程已退出")
                try:
                    r = client.get(f"{inst.base_url}{path}", headers=headers)
                    if r.status_code == 200:
                        return
                    last_err = f"HTTP {r.status_code}"
                except httpx.HTTPError as e:
                    last_err = str(e)
                time.sleep(0.3)
        raise RuntimeError(f"健康检查超时: {last_err}")

    def _alloc_port(self) -> int:
        settings = get_settings()
        with self._lock:
            for port in range(settings.port_range_start, settings.port_range_end + 1):
                if port in self._used_ports:
                    continue
                if self._port_free(port):
                    self._used_ports.add(port)
                    return port
        raise RuntimeError("没有空闲端口")

    @staticmethod
    def _port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def _render_command(
        self,
        plugin: Dict[str, Any],
        *,
        port: int,
        token: str,
        gpu_index: Optional[int],
    ) -> List[str]:
        plugin_id = str(plugin["id"])
        return [
            self._render_str(
                part,
                port=port,
                token=token,
                gpu_index=gpu_index,
                plugin_id=plugin_id,
            )
            for part in plugin["command"]
        ]

    @staticmethod
    def _render_str(
        s: str,
        *,
        port: int,
        token: str,
        gpu_index: Optional[int],
        plugin_id: str,
    ) -> str:
        return (
            s.replace("{python}", sys.executable)
            .replace("{port}", str(port))
            .replace("{token}", token)
            .replace(
                "{cuda_visible_devices}",
                "" if gpu_index is None else str(gpu_index),
            )
            .replace("{plugin_id}", plugin_id)
        )

    def _reap_locked(self) -> None:
        dead = [
            iid
            for iid, inst in self._instances.items()
            if inst.process.poll() is not None
        ]
        for iid in dead:
            inst = self._instances.pop(iid)
            self._used_ports.discard(inst.port)

    @staticmethod
    def _public(inst: Instance) -> Dict[str, Any]:
        alive = inst.process.poll() is None
        return {
            "instance_id": inst.instance_id,
            "plugin_id": inst.plugin_id,
            "base_url": inst.base_url,
            "token": inst.token,
            "gpu_index": inst.gpu_index,
            "pid": inst.pid,
            "port": inst.port,
            "state": inst.state if alive else "stopped",
            "alive": alive,
            "started_at": inst.started_at,
        }


manager = ProcessManager()
