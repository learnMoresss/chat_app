"""安卓画面简易 HTTP（MJPEG/HTML），供 redroid 等无 noVNC 的后端观看。

支持多端口：每台实例各自 host_port，互不抢唯一全局服务。
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional, Tuple

_lock = threading.Lock()
# port -> (server, thread, png_fn)
_servers: Dict[int, Tuple[ThreadingHTTPServer, threading.Thread, Callable[[], bytes]]] = {}


def _make_handler(png_fn_holder: Dict[str, Callable[[], bytes]]):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle(write_body=False)

        def do_GET(self) -> None:  # noqa: N802
            self._handle(write_body=True)

        def _handle(self, *, write_body: bool) -> None:
            path = (self.path or "/").split("?", 1)[0]
            if path in ("/", "/index.html", "/vnc.html"):
                body = (
                    b"<!DOCTYPE html><html><head><meta charset=utf-8>"
                    b"<meta name=viewport content='width=device-width,initial-scale=1'>"
                    b"<title>Android</title>"
                    b"<style>html,body{margin:0;background:#111;height:100%;}"
                    b"img{display:block;max-width:100%;max-height:100vh;margin:0 auto}</style>"
                    b"</head><body>"
                    b"<img id=s alt=screen>"
                    b"<script>"
                    b"const i=document.getElementById('s');"
                    b"function tick(){i.src='shot.png?t='+Date.now()}"
                    b"i.onload=()=>setTimeout(tick,800);i.onerror=()=>setTimeout(tick,1500);tick();"
                    b"</script></body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if write_body:
                    self.wfile.write(body)
                return
            if path == "/shot.png":
                fn = png_fn_holder.get("fn")
                if not fn:
                    self.send_error(503, "no screenshot provider")
                    return
                try:
                    png = fn()
                except Exception as e:  # noqa: BLE001
                    self.send_error(502, str(e)[:200])
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                if write_body:
                    self.wfile.write(png)
                return
            self.send_error(404)

    return _Handler


def ensure_live_server(*, host: str = "127.0.0.1", port: int = 16082, png_fn: Callable[[], bytes]) -> str:
    """启动（或更新）指定端口的观看服务，返回本机 base URL。"""
    port = int(port)
    with _lock:
        if port in _servers:
            srv, th, _old = _servers[port]
            holder = getattr(srv, "_png_holder", None)
            if isinstance(holder, dict):
                holder["fn"] = png_fn
            _servers[port] = (srv, th, png_fn)
            return f"http://{host}:{port}/"
        holder: Dict[str, Callable[[], bytes]] = {"fn": png_fn}
        try:
            srv = ThreadingHTTPServer((host, port), _make_handler(holder))
        except OSError as e:
            if getattr(e, "errno", None) in (98, 48):  # EADDRINUSE
                return f"http://{host}:{port}/"
            raise
        setattr(srv, "_png_holder", holder)

        def _run() -> None:
            srv.serve_forever(poll_interval=0.5)

        t = threading.Thread(target=_run, name=f"android-live-{port}", daemon=True)
        t.start()
        _servers[port] = (srv, t, png_fn)
        return f"http://{host}:{port}/"
