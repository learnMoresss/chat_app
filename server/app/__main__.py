from __future__ import annotations

import argparse
import sys

import uvicorn

from .config import get_settings


def cmd_serve() -> None:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


def cmd_reset_password(username: str, password: str) -> None:
    from . import db
    from .security import hash_password

    db.connect()
    row = db.fetchone(
        "SELECT id, username FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
    )
    if not row:
        print(f"用户不存在: {username}", file=sys.stderr)
        sys.exit(1)
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(password), row["id"]),
    )
    print(f"已重置 {row['username']} 的密码")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("serve", help="启动 HTTP 服务（默认）")

    rp = sub.add_parser("reset-password", help="重置用户密码")
    rp.add_argument("username")
    rp.add_argument("password")

    args, unknown = parser.parse_known_args()
    # 兼容旧用法：无子命令时直接 serve
    if args.cmd is None and not unknown:
        cmd_serve()
        return
    if args.cmd == "reset-password":
        cmd_reset_password(args.username, args.password)
        return
    if args.cmd in (None, "serve"):
        cmd_serve()
        return
    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
