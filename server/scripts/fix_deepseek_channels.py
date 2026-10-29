#!/usr/bin/env python3
"""确保 DeepSeek 官方订阅 + 日卡两条公共渠道并存，并修正日卡 model id。"""
from __future__ import annotations

import os
from pathlib import Path

from app import db
from app.crypto import encrypt_secret
from app.security import iso, new_id


def _load_gateway_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for p in (
        Path("/home/joy/chat_app/dsh-product/.dsh-home/gateway.env"),
        Path(__file__).resolve().parents[2] / "dsh-product" / ".dsh-home" / "gateway.env",
    ):
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            env[k.strip()] = v.strip().strip("\"'")
    # also process env
    for k in ("DEEPSEEK_API_KEY_OFFICIAL", "DEEPSEEK_API_KEY", "DAYCARD_DEEPSEEK_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def main() -> None:
    env = _load_gateway_env()

    # --- 日卡 ---
    day = db.fetchone(
        """
        SELECT * FROM channels
        WHERE name LIKE '%日卡%' OR lower(base_url) LIKE '%8888ok%'
        ORDER BY created_at LIMIT 1
        """
    )
    if day:
        model = (day["model"] or "").strip()
        if "/" in model:
            left, right = model.split("/", 1)
            if left in ("daycard-deepseek", "deepseek-official", "local-qwen"):
                model = right.strip()
        if not model:
            model = "deepseek-v4.1-flash"
        db.execute(
            "UPDATE channels SET name=?, model=?, base_url=? WHERE id=?",
            ("DeepSeek日卡", model, "https://8888ok.cc/v1", day["id"]),
        )
        print("fixed_daycard", day["id"], model)
    else:
        day_key = env.get("DAYCARD_DEEPSEEK_API_KEY") or ""
        cid = new_id("ch_")
        db.execute(
            """
            INSERT INTO channels(id, user_id, name, model, base_url, key_enc, is_shared, created_at)
            VALUES (?, NULL, ?, ?, ?, ?, 1, ?)
            """,
            (
                cid,
                "DeepSeek日卡",
                "deepseek-v4.1-flash",
                "https://8888ok.cc/v1",
                encrypt_secret(day_key) if day_key else "",
                iso(),
            ),
        )
        print("created_daycard", cid, "key", bool(day_key))

    # --- 官方 ---
    official_key = env.get("DEEPSEEK_API_KEY_OFFICIAL") or env.get("DEEPSEEK_API_KEY") or ""
    off = db.fetchone(
        """
        SELECT * FROM channels
        WHERE lower(base_url) LIKE '%api.deepseek.com%'
        ORDER BY created_at LIMIT 1
        """
    )
    if off is None:
        cid = new_id("ch_")
        db.execute(
            """
            INSERT INTO channels(id, user_id, name, model, base_url, key_enc, is_shared, created_at)
            VALUES (?, NULL, ?, ?, ?, ?, 1, ?)
            """,
            (
                cid,
                "DeepSeek官方订阅",
                "deepseek-flash",
                "https://api.deepseek.com",
                encrypt_secret(official_key) if official_key else "",
                iso(),
            ),
        )
        print("created_official", cid, "key", bool(official_key))
    else:
        model = (off["model"] or "deepseek-flash").strip()
        if model.startswith("daycard-") or "v4" in model or "8888" in model or "/" in model:
            model = "deepseek-flash"
        key_enc = off["key_enc"] or ""
        if not key_enc and official_key:
            key_enc = encrypt_secret(official_key)
        db.execute(
            "UPDATE channels SET name=?, model=?, base_url=?, key_enc=? WHERE id=?",
            (
                "DeepSeek官方订阅",
                model,
                "https://api.deepseek.com",
                key_enc,
                off["id"],
            ),
        )
        print("updated_official", off["id"], model, "key", bool(key_enc))

    rows = db.fetchall(
        """
        SELECT id, name, model, base_url, length(key_enc) AS klen
        FROM channels
        WHERE name LIKE '%DeepSeek%' OR name LIKE '%日卡%'
           OR lower(base_url) LIKE '%deepseek%' OR lower(base_url) LIKE '%8888ok%'
        ORDER BY name
        """
    )
    for r in rows:
        print("CH", dict(r))


if __name__ == "__main__":
    main()
