"""虚拟设备操控：隔离 docker 桌面 + 安卓 adb。供 DSH / 产品网关调用。

桌面默认走 docker_novnc 容器内 DISPLAY（非主机 :0）。
host_x11 仅保留显式调试函数，产品 API 应对 docker 设备调用 docker_*。
"""
from __future__ import annotations

import base64
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import get_settings

ROOT = Path.home() / "opt" / "vnc-root" / "usr" / "bin"
XDOTOOL = str(ROOT / "xdotool") if (ROOT / "xdotool").is_file() else (shutil.which("xdotool") or "xdotool")
SCROT = str(ROOT / "scrot") if (ROOT / "scrot").is_file() else (shutil.which("scrot") or "scrot")
IMPORT_BIN = shutil.which("import") or "import"

def _docker_display() -> str:
    try:
        d = (get_settings().desktop_docker_display or "").strip()
    except Exception:
        d = ""
    return os.environ.get("CHATDESK_DISPLAY") or d or ":1"


def _env_display() -> Dict[str, str]:
    env = os.environ.copy()
    env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
    auth = os.environ.get("XAUTHORITY") or "/run/user/1000/gdm/Xauthority"
    if Path(auth).is_file():
        env["XAUTHORITY"] = auth
    # xdotool from extracted deb may need libs
    lib = str(Path.home() / "opt" / "vnc-root" / "usr" / "lib" / "x86_64-linux-gnu")
    if Path(lib).is_dir():
        env["LD_LIBRARY_PATH"] = lib + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    return env


def _run(cmd: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=_env_display(),
    )


def desktop_geometry() -> Dict[str, int]:
    r = _run([XDOTOOL, "getdisplaygeometry"])
    if r.returncode != 0:
        return {"width": 1024, "height": 768}
    parts = (r.stdout or "").strip().split()
    try:
        return {"width": int(parts[0]), "height": int(parts[1])}
    except Exception:
        return {"width": 1024, "height": 768}


def desktop_screenshot_png() -> bytes:
    """截主机桌面。mss 读进程环境 DISPLAY，须先注入 :0（uvicorn 常无 DISPLAY）。"""
    env = _env_display()
    saved = {k: os.environ.get(k) for k in ("DISPLAY", "XAUTHORITY", "LD_LIBRARY_PATH")}
    try:
        for k in ("DISPLAY", "XAUTHORITY", "LD_LIBRARY_PATH"):
            if env.get(k):
                os.environ[k] = env[k]
        try:
            import mss as mss_mod
            from mss import tools as mss_tools

            factory = getattr(mss_mod, "MSS", None) or mss_mod.mss
            with factory() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(mon)
                return mss_tools.to_png(shot.rgb, shot.size)
        except Exception:
            pass
        # scrot 对 /tmp 偶发失败；落到 runtime
        path = str(Path.home() / "chat_app" / "runtime" / f"desk-shot-{os.getpid()}.png")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            r = _run([SCROT, "-z", "-o", path], timeout=20)
            if r.returncode != 0 or not Path(path).is_file() or Path(path).stat().st_size < 32:
                raise RuntimeError((r.stderr or r.stdout or "screenshot failed")[:300])
            return Path(path).read_bytes()
        finally:
            Path(path).unlink(missing_ok=True)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def desktop_click(x: int, y: int, *, button: int = 1) -> None:
    r = _run([XDOTOOL, "mousemove", "--sync", str(int(x)), str(int(y)), "click", str(int(button))])
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "click failed")[:300])


def desktop_type(text: str) -> None:
    # 避免 shell 转义：用 type --args
    r = _run([XDOTOOL, "type", "--clearmodifiers", "--", text[:2000]])
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "type failed")[:300])


def desktop_key(key: str) -> None:
    r = _run([XDOTOOL, "key", "--clearmodifiers", key.strip()[:64]])
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "key failed")[:300])


def _docker_bin() -> str:
    d = shutil.which("docker")
    if not d:
        raise RuntimeError("未安装 docker")
    return d


def docker_container_name(meta: Optional[Dict[str, Any]] = None, *, external_id: str = "") -> str:
    m = meta or {}
    c = str(m.get("container") or "").strip()
    if c:
        return c
    eid = (external_id or str(m.get("external_id") or "")).strip()
    if eid.startswith("chatdesk_"):
        return eid
    if eid:
        return f"chatdesk_{eid}"
    raise RuntimeError("缺少 docker 容器名")


def _docker_exec(container: str, bash_cmd: str, *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    docker = _docker_bin()
    return subprocess.run(
        [docker, "exec", container, "bash", "-lc", bash_cmd],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def docker_desktop_geometry(container: str) -> Dict[str, int]:
    disp = shlex.quote(_docker_display())
    r = _docker_exec(
        container,
        f"export DISPLAY={disp}; "
        "xdotool getdisplaygeometry 2>/dev/null || echo 1280 800",
    )
    parts = (r.stdout or "").strip().split()
    try:
        return {"width": int(parts[0]), "height": int(parts[1])}
    except Exception:
        return {"width": 1280, "height": 800}


def docker_desktop_screenshot_png(container: str) -> bytes:
    """容器内截图：优先 import/scrot。"""
    remote = f"/tmp/chatdesk-shot-{os.getpid()}.png"
    disp = shlex.quote(_docker_display())
    script = (
        f"set -e; export DISPLAY={disp}; rm -f {remote}; "
        f"if command -v import >/dev/null 2>&1; then import -window root {remote}; "
        f"elif command -v scrot >/dev/null 2>&1; then scrot -z -o {remote}; "
        f"elif command -v xwd >/dev/null 2>&1 && command -v convert >/dev/null 2>&1; then "
        f"xwd -root -silent | convert xwd:- png:{remote}; "
        f"else echo 'no screenshot tool' >&2; exit 1; fi; "
        f"test -s {remote}"
    )
    r = _docker_exec(container, script, timeout=40)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "docker screenshot failed")[:400])
    docker = _docker_bin()
    out = subprocess.run(
        [docker, "exec", container, "cat", remote],
        check=False,
        capture_output=True,
        timeout=20,
    )
    _docker_exec(container, f"rm -f {remote}", timeout=10)
    if out.returncode != 0 or not out.stdout or len(out.stdout) < 32:
        raise RuntimeError("docker screenshot empty")
    return out.stdout


def docker_desktop_click(container: str, x: int, y: int, *, button: int = 1) -> None:
    disp = shlex.quote(_docker_display())
    r = _docker_exec(
        container,
        f"export DISPLAY={disp}; "
        f"xdotool mousemove --sync {int(x)} {int(y)} click {int(button)}",
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "docker click failed")[:300])


def docker_desktop_type(container: str, text: str) -> None:
    # 避免引号：用 printf %q
    q = shlex.quote(text[:2000])
    disp = shlex.quote(_docker_display())
    r = _docker_exec(
        container,
        f"export DISPLAY={disp}; xdotool type --clearmodifiers -- {q}",
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "docker type failed")[:300])


def docker_desktop_key(container: str, key: str) -> None:
    k = shlex.quote(key.strip()[:64])
    disp = shlex.quote(_docker_display())
    r = _docker_exec(
        container,
        f"export DISPLAY={disp}; xdotool key --clearmodifiers {k}",
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "docker key failed")[:300])


def docker_desktop_windows(container: str, *, limit: int = 40) -> List[Dict[str, Any]]:
    """容器内可见窗口列表（wmctrl/xdotool），作桌面「控件树」近似。"""
    import json as _json

    disp = shlex.quote(_docker_display())
    lim = max(1, min(int(limit), 80))
    # 先装工具（幂等），再导出 wmctrl；失败则 xdotool 列表
    prep = (
        f"export DISPLAY={disp}; "
        "command -v wmctrl >/dev/null 2>&1 || "
        "(apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wmctrl) >/dev/null 2>&1 || true; "
        "if command -v wmctrl >/dev/null 2>&1; then wmctrl -lG; "
        "else echo '__XDOTOOL__'; xdotool search --onlyvisible --name '.*' 2>/dev/null || true; fi"
    )
    r = _docker_exec(container, prep, timeout=90)
    out = (r.stdout or "").strip()
    elements: List[Dict[str, Any]] = []
    if out.startswith("__XDOTOOL__"):
        ids = [x for x in out.split()[1:] if x.isdigit()][:lim]
        for i, wid in enumerate(ids, 1):
            g = _docker_exec(
                container,
                f"export DISPLAY={disp}; xdotool getwindowgeometry --shell {shlex.quote(wid)}; "
                f"echo NAME=$(xdotool getwindowname {shlex.quote(wid)} 2>/dev/null || true)",
                timeout=15,
            )
            kv = dict(re.findall(r"(\w+)=(.*)", g.stdout or ""))
            try:
                x, y = int(kv.get("X", 0)), int(kv.get("Y", 0))
                w, h = int(kv.get("WIDTH", 0)), int(kv.get("HEIGHT", 0))
            except Exception:
                x = y = w = h = 0
            title = (kv.get("NAME") or "").strip() or wid
            elements.append(
                {
                    "ref": f"@w{i}",
                    "window_id": wid,
                    "text": title,
                    "content_desc": title,
                    "bounds": [x, y, x + w, y + h],
                    "center": [x + max(w, 1) // 2, y + max(h, 1) // 2],
                    "clickable": True,
                    "class": "window",
                }
            )
        return elements

    for i, ln in enumerate([x for x in out.splitlines() if x.strip()][:lim], 1):
        parts = ln.split(None, 7)
        if len(parts) < 8:
            continue
        wid, _desk, xs, ys, ws, hs, _host, title = parts
        try:
            x, y, w, h = int(xs), int(ys), int(ws), int(hs)
        except Exception:
            continue
        elements.append(
            {
                "ref": f"@w{i}",
                "window_id": wid,
                "text": title,
                "content_desc": title,
                "bounds": [x, y, x + w, y + h],
                "center": [x + w // 2, y + h // 2],
                "clickable": True,
                "class": "window",
            }
        )
    # 避免未使用告警；解析失败时返回空列表而非抛
    _ = _json
    return elements


def docker_desktop_find_windows(
    container: str,
    *,
    text: str = "",
    limit: int = 20,
) -> List[Dict[str, Any]]:
    q = (text or "").strip().lower()
    hits = []
    for el in docker_desktop_windows(container, limit=80):
        title = str(el.get("text") or "").lower()
        if q and q not in title:
            continue
        hits.append(el)
        if len(hits) >= limit:
            break
    return hits


def docker_desktop_tap_ref(container: str, ref: str) -> Dict[str, Any]:
    """按 @wN 聚焦窗口并点中心；也可传窗口 id。"""
    key = (ref or "").strip()
    windows = docker_desktop_windows(container, limit=80)
    hit = None
    if key.startswith("@w"):
        hit = next((w for w in windows if w.get("ref") == key), None)
    elif key:
        hit = next((w for w in windows if str(w.get("window_id")) == key), None)
    if hit is None:
        raise RuntimeError(f"未找到窗口 ref={key}")
    wid = shlex.quote(str(hit["window_id"]))
    disp = shlex.quote(_docker_display())
    cx, cy = int(hit["center"][0]), int(hit["center"][1])
    r = _docker_exec(
        container,
        f"export DISPLAY={disp}; xdotool windowactivate --sync {wid} mousemove --sync {cx} {cy} click 1",
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "desktop tap failed")[:300])
    return {"ok": True, "ref": hit.get("ref"), "x": cx, "y": cy, "text": hit.get("text") or ""}


def android_key(key: str, serial: str = "127.0.0.1:5555") -> None:
    """adb keyevent；支持 KEYCODE_* 名或数字。"""
    adb = _adb_connect(serial)
    raw = (key or "").strip()
    if not raw:
        raise RuntimeError("empty key")
    # 常见别名
    aliases = {
        "back": "KEYCODE_BACK",
        "home": "KEYCODE_HOME",
        "enter": "KEYCODE_ENTER",
        "return": "KEYCODE_ENTER",
        "menu": "KEYCODE_MENU",
        "power": "KEYCODE_POWER",
        "delete": "KEYCODE_DEL",
        "del": "KEYCODE_DEL",
    }
    code = aliases.get(raw.lower(), raw)
    r = subprocess.run(
        [adb, "-s", serial, "shell", "input", "keyevent", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "keyevent failed")[:300])


def android_adb_serial(meta: Optional[Dict[str, Any]] = None) -> str:
    return str((meta or {}).get("adb_serial") or "127.0.0.1:5555")


def _adb_bin() -> str:
    cand = [
        shutil.which("adb") or "",
        str(Path.home() / "opt" / "platform-tools" / "adb"),
        "/usr/lib/android-sdk/platform-tools/adb",
    ]
    for p in cand:
        if p and Path(p).is_file():
            return p
    raise RuntimeError("未安装 adb（期望 ~/opt/platform-tools/adb）")


def _adb_connect(serial: str) -> str:
    adb = _adb_bin()
    if ":" in serial:
        subprocess.run([adb, "connect", serial], check=False, capture_output=True, timeout=15)
    return adb


def android_screenshot_png(serial: str = "127.0.0.1:5555") -> bytes:
    adb = _adb_connect(serial)
    r = subprocess.run(
        [adb, "-s", serial, "exec-out", "screencap", "-p"],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError((r.stderr or b"adb screencap failed").decode("utf-8", "ignore")[:300])
    return r.stdout


def android_tap(x: int, y: int, serial: str = "127.0.0.1:5555") -> None:
    adb = _adb_connect(serial)
    r = subprocess.run(
        [adb, "-s", serial, "shell", "input", "tap", str(int(x)), str(int(y))],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "tap failed")[:300])


def android_text(text: str, serial: str = "127.0.0.1:5555") -> None:
    adb = _adb_connect(serial)
    safe = text.replace(" ", "%s")[:500]
    r = subprocess.run(
        [adb, "-s", serial, "shell", "input", "text", safe],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "input text failed")[:300])


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _parse_bounds(raw: str) -> Optional[Tuple[int, int, int, int]]:
    m = _BOUNDS_RE.match((raw or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def android_ui_dump_xml(serial: str = "127.0.0.1:5555") -> str:
    """uiautomator dump → XML（与 Accessibility 同源；真机 USB / 模拟器 / redroid 通用）。"""
    adb = _adb_connect(serial)
    # 优先 exec-out 直出，避免落盘权限问题
    r = subprocess.run(
        [adb, "-s", serial, "exec-out", "uiautomator", "dump", "/dev/tty"],
        check=False,
        capture_output=True,
        timeout=45,
    )
    out = (r.stdout or b"").decode("utf-8", "ignore")
    if "hierarchy" in out or "<node" in out:
        # 去掉尾部 UI hierchary dumped to: ... 行
        if "UI hierchary dumped to:" in out:
            out = out.split("UI hierchary dumped to:")[0]
        if "UI hierarchy dumped to:" in out:
            out = out.split("UI hierarchy dumped to:")[0]
        return out.strip()
    remote = "/sdcard/window_dump.xml"
    r2 = subprocess.run(
        [adb, "-s", serial, "shell", "uiautomator", "dump", remote],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if r2.returncode != 0:
        raise RuntimeError((r2.stderr or r2.stdout or "uiautomator dump failed")[:400])
    r3 = subprocess.run(
        [adb, "-s", serial, "exec-out", "cat", remote],
        check=False,
        capture_output=True,
        timeout=20,
    )
    xml = (r3.stdout or b"").decode("utf-8", "ignore")
    if "<node" not in xml and "hierarchy" not in xml:
        raise RuntimeError("uiautomator dump empty")
    return xml


def android_ui_elements(
    serial: str = "127.0.0.1:5555",
    *,
    interactive_only: bool = True,
    limit: int = 80,
) -> List[Dict[str, Any]]:
    """解析控件树为带 ref 的元素列表，供 Agent 按 @eN 点击。"""
    xml = android_ui_dump_xml(serial)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise RuntimeError(f"ui xml parse failed: {e}") from e
    elements: List[Dict[str, Any]] = []
    idx = 0
    for node in root.iter("node"):
        clickable = (node.attrib.get("clickable") or "").lower() == "true"
        long_clickable = (node.attrib.get("long-clickable") or "").lower() == "true"
        checkable = (node.attrib.get("checkable") or "").lower() == "true"
        enabled = (node.attrib.get("enabled") or "true").lower() != "false"
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        rid = (node.attrib.get("resource-id") or "").strip()
        cls = (node.attrib.get("class") or "").strip()
        bounds = _parse_bounds(node.attrib.get("bounds") or "")
        if bounds is None:
            continue
        x1, y1, x2, y2 = bounds
        if x2 <= x1 or y2 <= y1:
            continue
        interactive = clickable or long_clickable or checkable or bool(text or desc)
        if interactive_only and not (clickable or long_clickable or checkable or text or desc):
            continue
        if not enabled and interactive_only:
            continue
        idx += 1
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        elements.append(
            {
                "ref": f"@e{idx}",
                "text": text,
                "content_desc": desc,
                "resource_id": rid,
                "class": cls,
                "clickable": clickable,
                "bounds": [x1, y1, x2, y2],
                "center": [cx, cy],
            }
        )
        if len(elements) >= max(1, limit):
            break
    return elements


def android_find_elements(
    serial: str,
    *,
    text: str = "",
    content_desc: str = "",
    resource_id: str = "",
    interactive_only: bool = False,
) -> List[Dict[str, Any]]:
    text_l = (text or "").strip().lower()
    desc_l = (content_desc or "").strip().lower()
    rid = (resource_id or "").strip()
    hits = []
    for el in android_ui_elements(serial, interactive_only=interactive_only, limit=200):
        if text_l and text_l not in (el.get("text") or "").lower():
            continue
        if desc_l and desc_l not in (el.get("content_desc") or "").lower():
            continue
        if rid and rid not in (el.get("resource_id") or ""):
            continue
        hits.append(el)
    return hits


def android_tap_ref(serial: str, ref: str, elements: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """按最近一次 dump 的 @eN 点击中心点。"""
    key = (ref or "").strip()
    if not key.startswith("@"):
        key = f"@{key}" if key.startswith("e") else key
    els = elements if elements is not None else android_ui_elements(serial, interactive_only=False, limit=200)
    hit = next((e for e in els if e.get("ref") == key), None)
    if hit is None:
        raise RuntimeError(f"找不到元素 {ref}，请重新 ui-dump")
    cx, cy = hit["center"]
    android_tap(int(cx), int(cy), serial)
    return {"ok": True, "ref": hit["ref"], "x": cx, "y": cy, "text": hit.get("text") or ""}


def list_adb_devices() -> List[Dict[str, str]]:
    """列出当前 adb 设备（含 USB 真机）。"""
    adb = _adb_bin()
    r = subprocess.run([adb, "devices", "-l"], check=False, capture_output=True, text=True, timeout=15)
    out: List[Dict[str, str]] = []
    for line in (r.stdout or "").splitlines()[1:]:
        line = line.strip()
        if not line or "offline" in line or "unauthorized" in line:
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        meta = {"serial": serial, "state": "device"}
        for p in parts[2:]:
            if ":" in p:
                k, v = p.split(":", 1)
                meta[k] = v
        out.append(meta)
    return out


def action_result_screenshot(png: bytes) -> Dict[str, Any]:
    return {
        "ok": True,
        "mime": "image/png",
        "png_base64": base64.b64encode(png).decode("ascii"),
        "bytes": len(png),
    }
