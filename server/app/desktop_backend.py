"""虚拟桌面后端：mock / aio_sandbox / docker_novnc（真容器 + noVNC）。

不跑第二套 Agent；只负责沙箱生命周期与观看 URL。
"""
from __future__ import annotations

import json
import secrets
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

import httpx

from .config import get_settings


def pick_free_localhost_port(
    preferred: int,
    *,
    span: Sequence[int] | None = None,
    exclude: Iterable[int] = (),
) -> int:
    """选本机空闲 TCP 端口；优先 preferred，再扫 span。"""
    blocked = {int(x) for x in exclude}
    ordered: list[int] = []
    for p in [int(preferred), *(span or ())]:
        if p > 0 and p not in blocked and p not in ordered:
            ordered.append(p)
    if not ordered:
        ordered = [int(preferred)]
    for port in ordered:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"无可用本机端口（preferred={preferred}）")


@dataclass
class DesktopInstance:
    external_id: str
    view_url: str
    control_url: str = ""
    meta: Optional[Dict[str, Any]] = None


class DesktopBackend:
    name: str = "base"

    def create(self, *, label: str = "") -> DesktopInstance:
        raise NotImplementedError

    def start(self, external_id: str) -> DesktopInstance:
        raise NotImplementedError

    def stop(self, external_id: str) -> None:
        raise NotImplementedError

    def destroy(self, external_id: str) -> None:
        raise NotImplementedError

    def refresh_urls(self, external_id: str) -> DesktopInstance:
        raise NotImplementedError


class MockDesktopBackend(DesktopBackend):
    name = "mock"

    def create(self, *, label: str = "") -> DesktopInstance:
        eid = f"mock_{secrets.token_hex(8)}"
        return DesktopInstance(
            external_id=eid,
            view_url=f"https://view.local/mock/{eid}",
            control_url=f"https://control.local/mock/{eid}",
            meta={"label": label, "backend": "mock"},
        )

    def start(self, external_id: str) -> DesktopInstance:
        return self.refresh_urls(external_id)

    def stop(self, external_id: str) -> None:
        return None

    def destroy(self, external_id: str) -> None:
        return None

    def refresh_urls(self, external_id: str) -> DesktopInstance:
        return DesktopInstance(
            external_id=external_id,
            view_url=f"https://view.local/mock/{external_id}",
            control_url=f"https://control.local/mock/{external_id}",
            meta={"backend": "mock"},
        )


class AioSandboxBackend(DesktopBackend):
    name = "aio_sandbox"

    def __init__(self, base_url: str, token: str = "", timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, headers=self._headers(), timeout=self.timeout)

    @staticmethod
    def _from_payload(data: Dict[str, Any], fallback_id: str = "") -> DesktopInstance:
        eid = str(data.get("id") or data.get("sandbox_id") or fallback_id)
        view = str(
            data.get("viewer_url")
            or data.get("vnc_url")
            or data.get("novnc_url")
            or data.get("view_url")
            or ""
        )
        control = str(data.get("control_url") or data.get("cdp_url") or view)
        return DesktopInstance(external_id=eid, view_url=view, control_url=control, meta=data)

    def create(self, *, label: str = "") -> DesktopInstance:
        with self._client() as c:
            r = c.post("sandboxes", json={"label": label or "chat-desktop"})
            r.raise_for_status()
            return self._from_payload(r.json())

    def start(self, external_id: str) -> DesktopInstance:
        with self._client() as c:
            r = c.post(f"sandboxes/{external_id}/start")
            r.raise_for_status()
            data = r.json() if r.content else {"id": external_id}
            return self._from_payload(data, fallback_id=external_id)

    def stop(self, external_id: str) -> None:
        with self._client() as c:
            r = c.post(f"sandboxes/{external_id}/stop")
            r.raise_for_status()

    def destroy(self, external_id: str) -> None:
        with self._client() as c:
            r = c.delete(f"sandboxes/{external_id}")
            r.raise_for_status()

    def refresh_urls(self, external_id: str) -> DesktopInstance:
        with self._client() as c:
            r = c.get(f"sandboxes/{external_id}")
            r.raise_for_status()
            return self._from_payload(r.json(), fallback_id=external_id)


def _public_view(path: str) -> str:
    s = get_settings()
    base = (s.public_base_url or "").rstrip("/")
    # public_base_url may be …/chat-api — desktop paths live on same host root
    if base.endswith("/chat-api"):
        base = base[: -len("/chat-api")]
    return f"{base}{path}"


def _docker_ok() -> bool:
    return bool(shutil.which("docker"))


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def _infer_novnc_internal_port(image: str, configured: int = 0) -> int:
    if configured and int(configured) > 0:
        return int(configured)
    low = (image or "").lower()
    if "dorowu" in low or "lxde-vnc" in low:
        return 80
    if "webtop" in low or "linuxserver" in low:
        return 3000
    # consol/ubuntu-xfce-vnc 等
    return 6901


class DockerNovncBackend(DesktopBackend):
    """本机 Docker 跑带 noVNC 的桌面（consol / dorowu / webtop）。"""

    name = "docker_novnc"

    def __init__(self) -> None:
        s = get_settings()
        self.image = (s.desktop_docker_image or "consol/ubuntu-xfce-vnc").strip()
        self.host_port = int(s.desktop_novnc_host_port or 16081)
        self.internal_port = _infer_novnc_internal_port(
            self.image, int(getattr(s, "desktop_docker_internal_port", 0) or 0)
        )
        self.display = (getattr(s, "desktop_docker_display", None) or ":1").strip() or ":1"
        self.public_path = (s.desktop_public_path or "/vd/desktop/").strip() or "/vd/desktop/"
        if not self.public_path.endswith("/"):
            self.public_path += "/"

    def _cname(self, external_id: str) -> str:
        return external_id if external_id.startswith("chatdesk_") else f"chatdesk_{external_id}"

    def _urls(self, eid: str) -> DesktopInstance:
        view = _public_view(self.public_path)
        return DesktopInstance(
            external_id=eid,
            view_url=view,
            control_url=view,
            meta={
                "backend": self.name,
                "image": self.image,
                "host_port": self.host_port,
                "internal_port": self.internal_port,
                "display": self.display,
                "public_path": self.public_path,
                "container": self._cname(eid),
            },
        )

    def create(self, *, label: str = "") -> DesktopInstance:
        if not _docker_ok():
            raise RuntimeError("未安装 docker，无法启动真桌面")
        eid = f"chatdesk_{secrets.token_hex(6)}"
        name = self._cname(eid)
        # 先清同名残留
        _run(["docker", "rm", "-f", name], check=False)
        base = int(self.host_port)
        host_port = pick_free_localhost_port(base, span=range(base, base + 40))
        reso = get_settings().desktop_resolution or "1280x800"
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--shm-size",
            "1gb",
            "--restart",
            "unless-stopped",
            "-p",
            f"127.0.0.1:{host_port}:{self.internal_port}",
            "-e",
            "VNC_PW=chatdesk",
            "-e",
            f"VNC_RESOLUTION={reso}",
            "-e",
            f"RESOLUTION={reso}",
            "-e",
            "PASSWORD=chatdesk",
            self.image,
        ]
        try:
            _run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"docker 启动桌面失败: {(e.stderr or e.stdout or '')[:400]}") from e
        # 等 noVNC 起来
        for _ in range(30):
            try:
                r = httpx.get(f"http://127.0.0.1:{host_port}/", timeout=2.0)
                if r.status_code < 500:
                    break
            except Exception:
                time.sleep(1)
        self._ensure_input_tools(name)
        inst = self._urls(eid)
        inst.meta = {
            **(inst.meta or {}),
            "label": label,
            "host_port": host_port,
            "container": name,
        }
        return inst

    def _ensure_input_tools(self, name: str) -> None:
        """容器内装 xdotool/scrot（幂等，失败不阻断观看）。"""
        _run(
            [
                "docker",
                "exec",
                name,
                "bash",
                "-lc",
                "command -v xdotool >/dev/null && command -v scrot >/dev/null && exit 0; "
                "(apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xdotool scrot imagemagick) "
                "|| (yum install -y -q xdotool scrot ImageMagick) || true; "
                "command -v wmctrl >/dev/null || "
                "(DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wmctrl) || true",
            ],
            check=False,
        )

    def start(self, external_id: str) -> DesktopInstance:
        name = self._cname(external_id)
        _run(["docker", "start", name], check=False)
        self._ensure_input_tools(name)
        return self._urls(external_id)

    def stop(self, external_id: str) -> None:
        _run(["docker", "stop", self._cname(external_id)], check=False)

    def destroy(self, external_id: str) -> None:
        _run(["docker", "rm", "-f", self._cname(external_id)], check=False)

    def refresh_urls(self, external_id: str) -> DesktopInstance:
        return self._urls(external_id)


class DockerAndroidBackend(DesktopBackend):
    """budtmo/docker-android（需 KVM）。noVNC 默认映射到 host 16082。"""

    name = "docker_android"

    def __init__(self) -> None:
        s = get_settings()
        self.image = (s.android_docker_image or "budtmo/docker-android:emulator_11.0").strip()
        self.host_port = int(s.android_novnc_host_port or 16082)
        self.public_path = (s.android_public_path or "/vd/android/").strip() or "/vd/android/"
        if not self.public_path.endswith("/"):
            self.public_path += "/"

    def _cname(self, external_id: str) -> str:
        return external_id if external_id.startswith("chatdroid_") else f"chatdroid_{external_id}"

    def _urls(self, eid: str) -> DesktopInstance:
        view = _public_view(self.public_path)
        return DesktopInstance(
            external_id=eid,
            view_url=view,
            control_url=view,
            meta={
                "backend": self.name,
                "image": self.image,
                "host_port": self.host_port,
                "public_path": self.public_path,
                "container": self._cname(eid),
                "kvm": True,
            },
        )

    def create(self, *, label: str = "") -> DesktopInstance:
        if not _docker_ok():
            raise RuntimeError("未安装 docker")
        import os

        if not os.path.exists("/dev/kvm"):
            raise RuntimeError("无 /dev/kvm，无法跑虚拟安卓")
        eid = f"chatdroid_{secrets.token_hex(6)}"
        name = self._cname(eid)
        _run(["docker", "rm", "-f", name], check=False)
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--privileged",
            "--device",
            "/dev/kvm",
            "--restart",
            "unless-stopped",
            "-e",
            "EMULATOR_DEVICE=Samsung Galaxy S10",
            "-e",
            "WEB_VNC=true",
            "-p",
            f"127.0.0.1:{self.host_port}:6080",
            self.image,
        ]
        try:
            _run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"docker 启动安卓失败: {(e.stderr or e.stdout or '')[:400]}") from e
        inst = self._urls(eid)
        inst.meta = {**(inst.meta or {}), "label": label}
        return inst

    def start(self, external_id: str) -> DesktopInstance:
        _run(["docker", "start", self._cname(external_id)], check=False)
        return self._urls(external_id)

    def stop(self, external_id: str) -> None:
        _run(["docker", "stop", self._cname(external_id)], check=False)

    def destroy(self, external_id: str) -> None:
        _run(["docker", "rm", "-f", self._cname(external_id)], check=False)

    def refresh_urls(self, external_id: str) -> DesktopInstance:
        return self._urls(external_id)


class HostX11Backend(DesktopBackend):
    """暴露 AI 主机本机图形会话（x11vnc + noVNC）。DSH 与用户看同一台真桌面。"""

    name = "host_x11"

    def __init__(self) -> None:
        s = get_settings()
        self.public_path = (s.desktop_public_path or "/vd/desktop/").strip() or "/vd/desktop/"
        if not self.public_path.endswith("/"):
            self.public_path += "/"

    def _inst(self, eid: str, label: str = "") -> DesktopInstance:
        view = _public_view(self.public_path)
        # noVNC 入口
        if not view.endswith("vnc.html"):
            view = view.rstrip("/") + "/vnc.html"
        return DesktopInstance(
            external_id=eid,
            view_url=view,
            control_url=view,
            meta={"backend": self.name, "display": ":0", "label": label, "note": "AI主机真桌面"},
        )

    def create(self, *, label: str = "") -> DesktopInstance:
        eid = f"hostx11_{secrets.token_hex(6)}"
        return self._inst(eid, label=label)

    def start(self, external_id: str) -> DesktopInstance:
        return self._inst(external_id)

    def stop(self, external_id: str) -> None:
        # 共享主机桌面：停止设备记录即可，不停掉物理会话
        return None

    def destroy(self, external_id: str) -> None:
        return None

    def refresh_urls(self, external_id: str) -> DesktopInstance:
        return self._inst(external_id)


class RedroidBackend(DesktopBackend):
    """redroid 容器 + adb 操控 + 简易 HTTP 画面（无内置 noVNC）。"""

    name = "redroid"

    def __init__(self) -> None:
        s = get_settings()
        self.image = (s.android_docker_image or "redroid/redroid:13.0.0-latest").strip()
        if "budtmo" in self.image:
            # budtmo 镜像偏旧且体积大；统一走 redroid 新版本
            self.image = "redroid/redroid:13.0.0-latest"
        self.host_port = int(s.android_novnc_host_port or 16082)
        self.adb_port = 5555
        self.public_path = (s.android_public_path or "/vd/android/").strip() or "/vd/android/"
        if not self.public_path.endswith("/"):
            self.public_path += "/"

    def _cname(self, external_id: str) -> str:
        return external_id if external_id.startswith("chatdroid_") else f"chatdroid_{external_id}"

    def _adb_serial(self) -> str:
        return f"127.0.0.1:{self.adb_port}"

    def _ensure_live(self) -> None:
        from . import device_actions as act
        from .android_live import ensure_live_server

        serial = self._adb_serial()

        def _png() -> bytes:
            return act.android_screenshot_png(serial)

        ensure_live_server(host="127.0.0.1", port=self.host_port, png_fn=_png)

    def _connect_adb(self) -> None:
        adb = shutil.which("adb") or str(Path.home() / "opt" / "platform-tools" / "adb")
        if not Path(adb).is_file() and not shutil.which("adb"):
            raise RuntimeError("未安装 adb")
        bin_adb = shutil.which("adb") or adb
        _run([bin_adb, "connect", self._adb_serial()], check=False)
        # redroid 冷启动可能 1～2 分钟
        for i in range(60):
            _run([bin_adb, "connect", self._adb_serial()], check=False)
            r = _run([bin_adb, "-s", self._adb_serial(), "get-state"], check=False)
            if (r.stdout or "").strip() == "device":
                boot = _run(
                    [bin_adb, "-s", self._adb_serial(), "shell", "getprop", "sys.boot_completed"],
                    check=False,
                )
                if (boot.stdout or "").strip() == "1" or i >= 20:
                    return
            time.sleep(3)
        raise RuntimeError("adb 未连上 redroid")

    def _urls(self, eid: str) -> DesktopInstance:
        self._ensure_live()
        view = _public_view(self.public_path)
        if not view.endswith("vnc.html"):
            view = view.rstrip("/") + "/vnc.html"
        return DesktopInstance(
            external_id=eid,
            view_url=view,
            control_url=view,
            meta={
                "backend": self.name,
                "image": self.image,
                "host_port": self.host_port,
                "adb_serial": self._adb_serial(),
                "container": self._cname(eid),
                "kvm": True,
            },
        )

    def create(self, *, label: str = "") -> DesktopInstance:
        if not _docker_ok():
            raise RuntimeError("未安装 docker")
        import os

        if not os.path.exists("/dev/kvm"):
            raise RuntimeError("无 /dev/kvm，无法跑虚拟安卓")
        # 本机无目标 tag 时，回退镜像站同版本或已有 redroid
        if not _image_present(self.image):
            for alt in (
                f"docker.m.daocloud.io/{self.image}" if not self.image.startswith("docker.") else "",
                "docker.m.daocloud.io/redroid/redroid:13.0.0-latest",
                "redroid/redroid:13.0.0-latest",
                "docker.m.daocloud.io/redroid/redroid:12.0.0-latest",
                "redroid/redroid:12.0.0-latest",
                "docker.m.daocloud.io/redroid/redroid:11.0.0-latest",
                "redroid/redroid:11.0.0-latest",
            ):
                if alt and _image_present(alt):
                    self.image = alt
                    break
        eid = f"chatdroid_{secrets.token_hex(6)}"
        name = self._cname(eid)
        _run(["docker", "rm", "-f", name], check=False)
        # binder 内核模块（redroid 必需）；无 sudo 时用特权容器 modprobe
        ensure = Path.home() / "chat_app" / "runtime" / "ensure-binder.sh"
        if ensure.is_file():
            _run(["bash", str(ensure)], check=False)
        adb_port = pick_free_localhost_port(
            int(self.adb_port),
            span=range(int(self.adb_port), int(self.adb_port) + 40),
        )
        live_port = pick_free_localhost_port(
            int(self.host_port),
            span=range(int(self.host_port), int(self.host_port) + 40),
            exclude=(adb_port,),
        )
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--privileged",
            "--device",
            "/dev/kvm",
            "--restart",
            "unless-stopped",
            "-v",
            "/dev/binderfs:/dev/binderfs",
            "-p",
            f"127.0.0.1:{adb_port}:5555",
            self.image,
            "androidboot.redroid_gpu_mode=guest",
        ]
        try:
            _run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"docker 启动 redroid 失败: {(e.stderr or e.stdout or '')[:400]}") from e
        # 本实例专用 adb/live 端口（避免第二台撞 5555/16082）
        self.adb_port = adb_port
        self.host_port = live_port
        time.sleep(12)
        self._connect_adb()
        inst = self._urls(eid)
        inst.meta = {
            **(inst.meta or {}),
            "label": label,
            "host_port": live_port,
            "adb_serial": f"127.0.0.1:{adb_port}",
            "container": name,
        }
        return inst

    def start(self, external_id: str) -> DesktopInstance:
        name = self._cname(external_id)
        _run(["docker", "start", name], check=False)
        time.sleep(5)
        # 从已有容器端口映射恢复 adb_port，避免写死 5555
        mapped = _docker_host_port(name, 5555)
        if mapped:
            self.adb_port = mapped
        try:
            self._connect_adb()
        except Exception:
            pass
        inst = self._urls(external_id)
        if mapped:
            inst.meta = {**(inst.meta or {}), "adb_serial": f"127.0.0.1:{mapped}"}
        return inst

    def stop(self, external_id: str) -> None:
        _run(["docker", "stop", self._cname(external_id)], check=False)

    def destroy(self, external_id: str) -> None:
        _run(["docker", "rm", "-f", self._cname(external_id)], check=False)

    def refresh_urls(self, external_id: str) -> DesktopInstance:
        return self._urls(external_id)


def get_desktop_backend() -> DesktopBackend:
    settings = get_settings()
    kind = (settings.desktop_backend or "mock").strip().lower()
    if kind in ("aio", "aio_sandbox", "sandbox"):
        base = (settings.desktop_aio_base_url or "").strip()
        if not base:
            raise RuntimeError("DESKTOP_BACKEND=aio_sandbox 但未配置 DESKTOP_AIO_BASE_URL")
        return AioSandboxBackend(base, token=settings.desktop_aio_token)
    if kind in ("host", "host_x11", "x11", "real"):
        return HostX11Backend()
    if kind in ("docker", "docker_novnc", "novnc"):
        return DockerNovncBackend()
    return MockDesktopBackend()


def _image_present(name: str) -> bool:
    if not _docker_ok():
        return False
    r = _run(["docker", "image", "inspect", name], check=False)
    return r.returncode == 0


def _docker_host_port(container: str, container_port: int) -> Optional[int]:
    """读取容器已发布端口（如 5555/tcp -> 主机端口）。"""
    r = _run(
        ["docker", "port", container, f"{int(container_port)}/tcp"],
        check=False,
    )
    out = (r.stdout or "").strip()
    if not out:
        return None
    # 127.0.0.1:5557
    tail = out.split()[0]
    if ":" not in tail:
        return None
    try:
        return int(tail.rsplit(":", 1)[-1])
    except ValueError:
        return None


def get_android_backend() -> DesktopBackend:
    """优先 redroid（按需启停 + 较新系统）；显式 budtmo 配置时才走 docker-android。"""
    settings = get_settings()
    preferred = (settings.android_docker_image or "").strip()
    if preferred:
        if "redroid" in preferred or preferred.startswith("docker.") and "redroid" in preferred:
            return RedroidBackend()
        if "budtmo" in preferred or "docker-android" in preferred:
            if _image_present(preferred):
                return DockerAndroidBackend()
            return RedroidBackend()
    for tag in (
        "redroid/redroid:13.0.0-latest",
        "docker.m.daocloud.io/redroid/redroid:13.0.0-latest",
        "redroid/redroid:12.0.0-latest",
        "redroid/redroid:11.0.0-latest",
        "docker.m.daocloud.io/redroid/redroid:11.0.0-latest",
    ):
        if _image_present(tag):
            return RedroidBackend()
    return RedroidBackend()
