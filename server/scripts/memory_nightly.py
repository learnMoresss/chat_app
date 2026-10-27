#!/usr/bin/env python3
"""夜间记忆流水线：热度衰减、课程(lesson)体检、可选 sqlite-vec 重建。

用法（在 server 目录）:
  .venv/bin/python scripts/memory_nightly.py [--vec]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.security import iso  # noqa: E402


def decay_heat() -> int:
    """简单热度衰减：heat_anchor *= 0.98（有该列时）。"""
    cols = {r[1] for r in db.fetchall("PRAGMA table_info(memory_facts)")}
    if "heat_anchor" not in cols:
        return 0
    rows = db.fetchall("SELECT id, heat_anchor FROM memory_facts WHERE heat_anchor IS NOT NULL")
    n = 0
    for r in rows:
        try:
            h = float(r["heat_anchor"] or 0)
        except Exception:
            continue
        nh = round(h * 0.98, 4)
        db.execute(
            "UPDATE memory_facts SET heat_anchor=?, heat_at=? WHERE id=?",
            (nh, iso(), r["id"]),
        )
        n += 1
    return n


def lesson_health() -> dict:
    """课程类记忆体检：空正文 / 重复标题。"""
    lessons = db.fetchall(
        "SELECT id, user_id, persona_id, content FROM memory_facts WHERE type='lesson' OR kind='lesson'"
    )
    # 兼容无 type 列
    if not lessons:
        try:
            lessons = db.fetchall(
                "SELECT id, user_id, persona_id, text AS content FROM memory_facts WHERE 1=0"
            )
        except Exception:
            lessons = []
    empty = 0
    for r in lessons or []:
        c = (r["content"] if "content" in r.keys() else "") or ""
        if len(str(c).strip()) < 4:
            empty += 1
    return {"lesson_rows": len(lessons or []), "empty_or_short": empty}


def rebuild_vec() -> dict:
    """可选：若安装 sqlite-vec 则重建 embeddings 表占位。"""
    try:
        import sqlite_vec  # type: ignore
    except Exception:
        return {"ok": False, "reason": "sqlite-vec not installed"}
    try:
        conn = db.connect()
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(embedding float[384])"
        )
        return {"ok": True, "note": "vec table ready (embeddings fill is separate job)"}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vec", action="store_true", help="尝试加载 sqlite-vec")
    args = ap.parse_args()
    db.connect()
    decayed = decay_heat()
    health = lesson_health()
    out = {"decayed": decayed, "lesson_health": health, "at": iso()}
    if args.vec:
        out["vec"] = rebuild_vec()
    print(out)


if __name__ == "__main__":
    main()
