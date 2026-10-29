#!/usr/bin/env python3
"""清洗脏记忆：字面量 / 问句 / 反悔 / 敏感词 / 实体键回填 / 同实体合并。默认 dry-run。

用法（server 目录）:
  .venv/bin/python scripts/cleanup_dirty_memories.py [--apply] [--persona-id PID]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.db import connect  # noqa: E402
from app.memory import (  # noqa: E402
    fact_hash,
    infer_entity_key,
    is_storable_fact,
    normalize_fact_text,
)
from app.companion.store import MemoryStore  # noqa: E402
from app.security import iso  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入；默认只预览")
    ap.add_argument("--persona-id", default="", help="仅处理此人设")
    ap.add_argument("--user-id", default="", help="仅处理此用户")
    ap.add_argument(
        "--persona-name-like",
        default="",
        help="按人设名前缀匹配（如 日卡）",
    )
    args = ap.parse_args()
    connect()

    persona_ids = set()
    if args.persona_id:
        persona_ids.add(args.persona_id)
    if args.persona_name_like:
        rows = db.fetchall(
            "SELECT id FROM personas WHERE name LIKE ?",
            (f"%{args.persona_name_like}%",),
        )
        for r in rows:
            persona_ids.add(r["id"])

    q = """
      SELECT id, user_id, persona_id, text, subject, attribute, status
      FROM memory_facts
      WHERE status IN ('active','legacy_unverified','candidate')
    """
    params: list = []
    if persona_ids:
        placeholders = ",".join("?" * len(persona_ids))
        q += f" AND persona_id IN ({placeholders})"
        params.extend(sorted(persona_ids))
    if args.user_id:
        q += " AND user_id=?"
        params.append(args.user_id)
    rows = db.fetchall(q, tuple(params))
    store = MemoryStore()
    dirty = 0
    noise = 0
    fixed = 0
    deleted = 0
    keyed = 0
    merged = 0
    by_entity: dict = {}

    for r in rows:
        raw = r["text"] or ""
        looks_dirty = raw.strip().startswith("{") or raw.strip().startswith("[") or "'fact'" in raw
        norm = normalize_fact_text(raw)
        storable = bool(norm) and is_storable_fact(norm)

        if looks_dirty:
            dirty += 1
            print(f"[dirty] {r['id'][:12]}… {raw[:60]!r} -> {norm!r} storable={storable}")
            if args.apply:
                if storable and norm != raw:
                    db.execute(
                        "UPDATE memory_facts SET text=?, updated_at=? WHERE id=?",
                        (norm, iso(), r["id"]),
                    )
                    try:
                        store._fts_upsert(r["id"], r["user_id"], r["persona_id"], norm)  # noqa: SLF001
                    except Exception:
                        pass
                    fixed += 1
                    raw = norm
                elif not storable:
                    db.execute(
                        "UPDATE memory_facts SET status='deleted', updated_at=? WHERE id=?",
                        (iso(), r["id"]),
                    )
                    deleted += 1
                    continue

        # 非 dirty 但噪音（问句/反悔/敏感）
        check_text = norm or raw
        if not is_storable_fact(check_text):
            noise += 1
            print(f"[noise] {r['id'][:12]}… {raw[:60]!r}")
            if args.apply:
                db.execute(
                    "UPDATE memory_facts SET status='deleted', updated_at=? WHERE id=?",
                    (iso(), r["id"]),
                )
                deleted += 1
            continue

        text = norm if (norm and is_storable_fact(norm)) else check_text
        if args.apply and norm and norm != (r["text"] or "") and is_storable_fact(norm):
            db.execute(
                "UPDATE memory_facts SET text=?, updated_at=? WHERE id=?",
                (norm, iso(), r["id"]),
            )
            fixed += 1

        subj, attr = (r["subject"] or "", r["attribute"] or "")
        # 回填或修正整句 attribute
        need_key = (not subj or not attr) or (attr and len(attr) > 10 and attr in text)
        if need_key:
            ns, na = infer_entity_key(text)
            if ns or na:
                subj, attr = ns, na
                print(f"[key] {r['id'][:12]}… -> {subj}/{attr}")
                if args.apply:
                    db.execute(
                        "UPDATE memory_facts SET subject=?, attribute=? WHERE id=?",
                        (subj, attr, r["id"]),
                    )
                    keyed += 1

        if subj and attr:
            ekey = (r["user_id"], r["persona_id"], subj, attr)
            by_entity.setdefault(ekey, []).append({"id": r["id"], "text": text, "row": r})

    for ekey, items in by_entity.items():
        if len(items) <= 1:
            continue
        items.sort(key=lambda x: len(x["text"] or ""), reverse=True)
        keep = items[0]
        for drop in items[1:]:
            print(f"[dup] keep={keep['id'][:12]} drop={drop['id'][:12]} {drop['text'][:40]!r}")
            if args.apply:
                try:
                    # 直接 supersede 重复条，避免 correct 再插一条
                    db.execute(
                        """
                        UPDATE memory_facts SET status='superseded', valid_to=?, updated_at=?
                        WHERE id=?
                        """,
                        (iso(), iso(), drop["id"]),
                    )
                    merged += 1
                except Exception as exc:
                    print(f"  merge fail: {exc}")

    print(
        f"done dirty={dirty} noise={noise} fixed={fixed} deleted={deleted} "
        f"keyed={keyed} merged={merged} apply={args.apply}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
