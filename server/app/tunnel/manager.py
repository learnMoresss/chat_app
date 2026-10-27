"""穿透进程管理：启动 / 停 / 读状态。"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .providers import build_command, catalog_dicts, extract_public_url

STATE_NAME = "tunnel_state.json"


def _state_path(data_dir: Path) -> Path:
    return data_dir / STATE_NAME


def load_state(data_dir: Path) -> Dict[str, Any]:
    p = _state_path(data_dir)
    if not p.is_file():
        return {
            "provider": "none",
            "active": False,
            "public_url": "",
            "pid": None,
            "error": "",
            "updated_at": 0,
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {
            "provider": "none",
            "active": False,
            "public_url": "",
            "pid": None,
            "error": "state_corrupt",
            "updated_at": 0,
        }


def save_state(data_dir: Path, state: Dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    p = _state_path(data_dir)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def provider_catalog() -> list:
    return catalog_dicts()


class TunnelManager:
    def __init__(self, data_dir: Path, *, local_port: int = 8000) -> None:
        self.data_dir = Path(data_dir)
        self.local_port = local_port
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def status(self) -> Dict[str, Any]:
        st = load_state(self.data_dir)
        pid = st.get("pid")
        if pid and not self._pid_alive(int(pid)):
            st["active"] = False
            st["pid"] = None
            if not st.get("error"):
                st["error"] = "process_exited"
            save_state(self.data_dir, st)
        return st

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            st = load_state(self.data_dir)
            pid = st.get("pid") or (self._proc.pid if self._proc else None)
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            elif pid:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except OSError:
                    pass
            self._proc = None
            st.update(
                {
                    "active": False,
                    "pid": None,
                    "public_url": st.get("public_url") or "",
                    "error": "",
                    "updated_at": int(time.time()),
                }
            )
            save_state(self.data_dir, st)
            return st

    def start(
        self,
        provider: str,
        *,
        ngrok_authtoken: str = "",
        cloudflared_token: str = "",
        ssh_config: str = "",
        ssh_host: str = "",
        ssh_remote_port: int = 0,
        wait_url_sec: float = 25.0,
    ) -> Dict[str, Any]:
        provider = (provider or "none").strip().lower()
        if provider in ("", "none", "lan", "off"):
            self.stop()
            st = {
                "provider": "none",
                "active": False,
                "public_url": "",
                "pid": None,
                "error": "",
                "updated_at": int(time.time()),
            }
            save_state(self.data_dir, st)
            return st

        self.stop()

        env = os.environ.copy()
        if provider == "ngrok" and ngrok_authtoken:
            env["NGROK_AUTHTOKEN"] = ngrok_authtoken

        try:
            cmd = build_command(
                provider,
                local_port=self.local_port,
                ngrok_authtoken=ngrok_authtoken,
                cloudflared_token=cloudflared_token,
                ssh_config=ssh_config,
                ssh_host=ssh_host,
                ssh_remote_port=ssh_remote_port,
            )
        except ValueError as e:
            st = {
                "provider": provider,
                "active": False,
                "public_url": "",
                "pid": None,
                "error": str(e),
                "updated_at": int(time.time()),
            }
            save_state(self.data_dir, st)
            return st

        log_path = self.data_dir / "tunnel.log"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "ab", buffering=0)

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            log_f.close()
            st = {
                "provider": provider,
                "active": False,
                "public_url": "",
                "pid": None,
                "error": f"binary_missing: {e}",
                "updated_at": int(time.time()),
            }
            save_state(self.data_dir, st)
            return st

        public_url = ""
        err = ""
        buf = []

        def _read() -> None:
            nonlocal public_url, err
            assert self._proc and self._proc.stdout
            for raw in iter(self._proc.stdout.readline, b""):
                if not raw:
                    break
                log_f.write(raw)
                line = raw.decode("utf-8", errors="replace")
                buf.append(line)
                u = extract_public_url(line)
                if u and not public_url:
                    public_url = u
                    st = load_state(self.data_dir)
                    st["public_url"] = u
                    st["updated_at"] = int(time.time())
                    save_state(self.data_dir, st)

        self._reader = threading.Thread(target=_read, name="tunnel-stdout", daemon=True)
        self._reader.start()

        deadline = time.time() + wait_url_sec
        while time.time() < deadline:
            if public_url:
                break
            if self._proc.poll() is not None:
                err = "process_exited_early"
                break
            time.sleep(0.3)

        # ssh_jump 通常没有 URL 输出，靠用户自己的域名
        if provider == "ssh_jump" and not public_url:
            public_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

        st = {
            "provider": provider,
            "active": self._proc.poll() is None,
            "public_url": public_url,
            "pid": self._proc.pid if self._proc.poll() is None else None,
            "error": err
            if err
            else ("" if (public_url or provider == "ssh_jump") else "url_not_detected_yet"),
            "updated_at": int(time.time()),
            "command": " ".join(cmd[:4]) + (" …" if len(cmd) > 4 else ""),
        }
        save_state(self.data_dir, st)
        return st
