"""穿透提供方定义与启动命令。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
import re
import shutil


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    cost: str
    needs_account: bool
    needs_binary: str  # 空=系统自带 ssh；否则为可执行文件名
    summary: str
    setup_hint: str


CATALOG: List[ProviderInfo] = [
    ProviderInfo(
        id="none",
        name="不穿透（仅局域网 / 已有公网）",
        cost="免费",
        needs_account=False,
        needs_binary="",
        summary="只监听本机；手机需同一 Wi‑Fi，或你已有域名/反向代理。",
        setup_hint="PUBLIC_TUNNEL_PROVIDER=none",
    ),
    ProviderInfo(
        id="cloudflared",
        name="Cloudflare Tunnel",
        cost="免费",
        needs_account=False,
        needs_binary="cloudflared",
        summary="临时 trycloudflare.com 免账号；长期聊天（SSE）建议配具名 Tunnel + CLOUDFLARED_TOKEN（免费 CF 账号）。",
        setup_hint="brew install cloudflared；具名隧道见 Cloudflare Zero Trust → Tunnels",
    ),
    ProviderInfo(
        id="ngrok",
        name="ngrok",
        cost="免费档",
        needs_account=True,
        needs_binary="ngrok",
        summary="稳定、支持长连接；免费账号需 NGROK_AUTHTOKEN（推荐作为免费档主力）。",
        setup_hint="https://ngrok.com → 注册拿 authtoken → ngrok config add-authtoken …",
    ),
    ProviderInfo(
        id="localhost_run",
        name="localhost.run",
        cost="免费",
        needs_account=False,
        needs_binary="",
        summary="系统 ssh 反代，零安装；免费域名会轮换（*.localhost.run / *.lhr.life）。",
        setup_hint="无需额外软件；本机需能访问 ssh 出网。",
    ),
    ProviderInfo(
        id="pinggy",
        name="Pinggy",
        cost="免费档",
        needs_account=False,
        needs_binary="",
        summary="ssh 反代到 a.pinggy.io，免费档有时长限制；出 https 地址。",
        setup_hint="无需安装；见 https://pinggy.io",
    ),
    ProviderInfo(
        id="ssh_jump",
        name="自建 SSH 反向隧道",
        cost="需自有 VPS",
        needs_account=True,
        needs_binary="",
        summary="你现在的腾讯云跳板方案：稳定、可控，适合自己长期部署。",
        setup_hint="配置 PUBLIC_TUNNEL_SSH_CONFIG / PUBLIC_TUNNEL_SSH_HOST（参考仓库外 公网访问-*/ssh-config）",
    ),
]


def catalog_dicts() -> List[Dict[str, object]]:
    out = []
    for p in CATALOG:
        binary_ok = True
        if p.needs_binary:
            binary_ok = shutil.which(p.needs_binary) is not None
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "cost": p.cost,
                "needs_account": p.needs_account,
                "needs_binary": p.needs_binary or None,
                "binary_installed": binary_ok if p.needs_binary else True,
                "summary": p.summary,
                "setup_hint": p.setup_hint,
            }
        )
    return out


# stdout 里抓公网 URL
_URL_RES = [
    re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I),
    re.compile(r"https://[a-z0-9.-]+\.ngrok(?:-free)?\.(?:app|dev|io)\S*", re.I),
    re.compile(r"https://[a-z0-9.-]+\.loca\.lt", re.I),
    re.compile(r"https://[a-z0-9.-]+\.pinggy\.(?:link|io)\S*", re.I),
    re.compile(r"https://[a-z0-9.-]+\.localhost\.run\S*", re.I),
    re.compile(r"https://[a-z0-9.-]+\.lhr\.(?:life|rocks)\S*", re.I),
    re.compile(r"https?://[a-z0-9.-]+\.(?:ngrok\.io|pinggy\.io)\S*", re.I),
]


_SKIP_HOSTS = ("admin.localhost.run", "localhost.run/docs", "github.com")


def extract_public_url(text: str) -> Optional[str]:
    for rx in _URL_RES:
        m = rx.search(text)
        if not m:
            continue
        url = m.group(0).rstrip(").,]\"'")
        low = url.lower()
        if any(s in low for s in _SKIP_HOSTS):
            continue
        return url
    return None


def build_command(
    provider: str,
    *,
    local_port: int,
    ngrok_authtoken: str = "",
    cloudflared_token: str = "",
    ssh_config: str = "",
    ssh_host: str = "",
    ssh_remote_port: int = 0,
) -> List[str]:
    if provider == "cloudflared":
        bin_path = shutil.which("cloudflared") or "cloudflared"
        if cloudflared_token:
            # 具名隧道（免费 CF 账号创建后拿到 token）
            return [bin_path, "tunnel", "run", "--token", cloudflared_token]
        return [bin_path, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{local_port}"]

    if provider == "ngrok":
        bin_path = shutil.which("ngrok") or "ngrok"
        # 新版 ngrok 用环境变量 NGROK_AUTHTOKEN；也可先 config
        return [bin_path, "http", str(local_port), "--log=stdout", "--log-format=term"]

    if provider == "localhost_run":
        # 官方：ssh -R 80:localhost:PORT nokey@localhost.run
        return [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ServerAliveInterval=30",
            "-R",
            f"80:127.0.0.1:{local_port}",
            "nokey@localhost.run",
        ]

    if provider == "pinggy":
        return [
            "ssh",
            "-p",
            "443",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ServerAliveInterval=30",
            "-R",
            f"0:127.0.0.1:{local_port}",
            "a.pinggy.io",
        ]

    if provider == "ssh_jump":
        if not ssh_config or not ssh_host:
            raise ValueError("ssh_jump 需要 PUBLIC_TUNNEL_SSH_CONFIG 与 PUBLIC_TUNNEL_SSH_HOST")
        remote = ssh_remote_port or local_port
        # 把本机 port 反代到跳板的 remote（跳板需另配 nginx/安全组）
        return [
            "ssh",
            "-F",
            ssh_config,
            "-N",
            "-o",
            "ServerAliveInterval=30",
            "-R",
            f"0.0.0.0:{remote}:127.0.0.1:{local_port}",
            ssh_host,
        ]

    raise ValueError(f"未知 provider: {provider}")
