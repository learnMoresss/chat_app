#!/usr/bin/env python3
"""快进模拟一天主动行为：世界线 tick + 预设聊天 → 统计私聊/朋友圈/触发原因。

用法（在 server 目录）：
  PYTHONPATH=. .venv/bin/python scripts/simulate_proactive_day.py
  PYTHONPATH=. .venv/bin/python scripts/simulate_proactive_day.py --days 3 --apply-pause-greet
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.companion.affect import AffectService  # noqa: E402
from app.companion.intent_service import IntentService  # noqa: E402
from app.companion.proactive import (  # noqa: E402
    ProactiveGates,
    ProactivePlanner,
    extract_followup_topics,
    schedule_chat_followups,
)
from app.companion.worldline import WorldlineService, set_worldline_enabled  # noqa: E402
from app.security import iso, new_id  # noqa: E402


UID = "u_908639b6beaee9cca4108a20"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_persona() -> tuple[str, str]:
    row = db.fetchone(
        "SELECT id FROM personas WHERE user_id=? AND name=? LIMIT 1",
        (UID, "主动行为模拟羊"),
    )
    if row:
        pid = row["id"]
    else:
        pid = new_id("p_")
        now = iso()
        db.execute(
            """
            INSERT INTO personas(
              id, user_id, name, bio, color, is_bot, greet, moments_on, created_at
            ) VALUES (?,?,?,?,?,?,1,1,?)
            """,
            (
                pid,
                UID,
                "主动行为模拟羊",
                "活泼的大学同学，住在杭州，喜欢画画散步咖啡。口语短句。",
                4280395775,
                1,
                now,
            ),
        )
    set_worldline_enabled(UID, pid, True)
    conv = db.fetchone(
        "SELECT id FROM conversations WHERE user_id=? AND persona_id=? LIMIT 1",
        (UID, pid),
    )
    if conv:
        cid = conv["id"]
    else:
        cid = new_id("c_")
        now = iso()
        db.execute(
            """
            INSERT INTO conversations(id, user_id, persona_id, preview, last_time)
            VALUES (?,?,?,?,?)
            """,
            (cid, UID, pid, "主动行为模拟", now),
        )
    # 确保 greet / moments 开
    try:
        db.execute(
            "UPDATE personas SET greet=1, moments_on=1, worldline_enabled=1 WHERE id=? AND user_id=?",
            (pid, UID),
        )
    except Exception:
        db.execute(
            "UPDATE personas SET greet=1, moments_on=1 WHERE id=? AND user_id=?",
            (pid, UID),
        )
    return pid, cid


def _pause_old_greets() -> int:
    before = db.fetchall(
        "SELECT id FROM schedules WHERE user_id=? AND kind LIKE 'greet_%' AND paused=0",
        (UID,),
    )
    db.execute(
        "UPDATE schedules SET paused=1, updated_at=? WHERE user_id=? AND kind LIKE 'greet_%'",
        (iso(), UID),
    )
    return len(before)


def _inject_user_msg(cid: str, pid: str, text: str, when: datetime) -> None:
    mid = new_id("m_")
    seq_row = db.fetchone("SELECT COALESCE(MAX(seq),0)+1 AS s FROM messages WHERE conv_id=?", (cid,))
    seq = int(seq_row["s"] if seq_row else 1)
    db.execute(
        """
        INSERT INTO messages(
          id, user_id, conv_id, client_id, sender_id, mine, kind, text, created_at, seq
        ) VALUES (?,?,?,?,?,?, 'text', ?, ?, ?)
        """,
        (mid, UID, cid, "sim-" + uuid.uuid4().hex[:12], UID, 1, text, when.isoformat(), seq),
    )
    db.execute(
        "UPDATE conversations SET last_time=? WHERE id=?",
        (when.isoformat(), cid),
    )


async def _simulate_one_day(day_idx: int, pid: str, cid: str) -> dict:
    wl = WorldlineService()
    # 强制可 tick：清空 next_wake
    db.execute(
        "UPDATE world_state SET next_wake_at='' WHERE user_id=? AND persona_id=?",
        (UID, pid),
    )
    await wl.tick(UID, pid)

    # 预设聊天：含跟进话题
    chats = [
        "嗨，最近怎么样",
        "下周一下午三点我有产品岗二面，有点紧张",
        "小夏下周生日，我想送她一套画笔",
    ]
    base = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    base = base + timedelta(days=day_idx)
    for i, text in enumerate(chats):
        when = base + timedelta(hours=i * 2)
        _inject_user_msg(cid, pid, text, when)
        schedule_chat_followups(UID, pid, text)
        AffectService().get_or_init(UID, pid)
        from app.companion.affect_rules import propose_after_reply

        propose_after_reply(
            user_id=UID,
            persona_id=pid,
            user_text=text,
            event_id=f"sim-{day_idx}-{i}-{uuid.uuid4().hex[:8]}",
        )
        ProactivePlanner().plan(UID, pid, trigger=f"sim_chat_d{day_idx}_{i}")

    # 再 tick 几次制造世界事件
    for _ in range(3):
        db.execute(
            "UPDATE world_state SET next_wake_at='' WHERE user_id=? AND persona_id=?",
            (UID, pid),
        )
        await wl.tick(UID, pid)
        ProactivePlanner().plan(UID, pid, trigger=f"sim_tick_d{day_idx}")

    # 模拟「过了一段时间」：把最近用户消息时间拨回，避免「刚聊过」挡执行
    old_cut = (_now_utc() - timedelta(hours=2)).isoformat()
    db.execute(
        """
        UPDATE messages SET created_at=?
        WHERE user_id=? AND conv_id=? AND mine=1
          AND created_at > ?
        """,
        (old_cut, UID, cid, (_now_utc() - timedelta(minutes=30)).isoformat()),
    )

    # 把 not_before 推到现在，尝试执行（不真正打模型：只标记 dry 统计）
    intents = IntentService().list_open(UID, pid, limit=50)
    for r in intents:
        if r.get("persona_id") == pid:
            db.execute(
                "UPDATE action_intents SET not_before='', status='ready' WHERE id=? AND status IN ('pending','ready')",
                (r["id"],),
            )
    due2 = [
        r
        for r in IntentService().due_executable(limit=80)
        if r["persona_id"] == pid and r["user_id"] == UID
    ]

    # 不调模型：按门禁模拟 accept/reject
    gates = ProactiveGates()
    sent_msg = []
    sent_moment = []
    blocked = []
    for intent in due2:
        kind = intent.get("action_kind") or ""
        reason = gates.check(UID, pid, action_kind=kind, for_execute=True)
        if reason:
            blocked.append({"id": intent["id"], "kind": kind, "reason": reason, "why": intent.get("reason_summary")})
            continue
        # 假执行
        IntentService().mark(intent["id"], "done", reason="sim")
        entry = {
            "id": intent["id"],
            "kind": kind,
            "reason": intent.get("reason_summary"),
            "source": intent.get("source"),
            "payload": intent.get("payload_json"),
        }
        if kind == "message":
            sent_msg.append(entry)
        elif kind == "moment":
            sent_moment.append(entry)

    bud = gates.budget_status(UID, pid)
    types = []
    for e in sent_msg + sent_moment:
        try:
            p = json.loads(e.get("payload") or "{}")
            types.append(p.get("candidate_type") or "unknown")
        except Exception:
            types.append("unknown")

    return {
        "day": day_idx,
        "messages": len(sent_msg),
        "moments": len(sent_moment),
        "blocked": blocked[:15],
        "trigger_types": types,
        "budget": bud,
        "followup_topics": extract_followup_topics("下周一下午三点我有产品岗二面"),
        "open_left": len(IntentService().list_open(UID, pid, limit=30)),
        "message_reasons": [e["reason"] for e in sent_msg],
        "moment_reasons": [e["reason"] for e in sent_moment],
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--apply-pause-greet", action="store_true")
    ap.add_argument("--out", default="/tmp/simulate_proactive_day.json")
    args = ap.parse_args()

    if args.apply_pause_greet:
        n = _pause_old_greets()
        print("paused_greet_schedules", n)

    pid, cid = _ensure_persona()
    print("PERSONA", pid, "CONV", cid)

    days = []
    send_hours = []
    for d in range(args.days):
        # 每天开始清空未执行意图，避免串天
        for r in IntentService().list_open(UID, pid, limit=100):
            IntentService().cancel(UID, pid, r["id"], reason="sim_day_reset")
        report = await _simulate_one_day(d, pid, cid)
        days.append(report)
        print(
            f"day{d}: msg={report['messages']} moment={report['moments']} "
            f"types={report['trigger_types']} blocked={len(report['blocked'])}"
        )
        # 记录「发送」时间戳分布（用现在 + day offset 伪）
        send_hours.append([10 + i for i in range(report["messages"])])

    # 连续三天同一刻检查：伪小时集合不应完全相同且条数在预算内
    same_slot = False
    if len(send_hours) >= 2:
        same_slot = all(send_hours[0] == h for h in send_hours[1:]) and bool(send_hours[0])

    over_budget = any(d["messages"] > 4 or d["moments"] > 2 for d in days)
    out = {
        "persona_id": pid,
        "conv_id": cid,
        "days": days,
        "same_exact_slots": same_slot,
        "over_budget": over_budget,
        "ok": (not over_budget) and any(d["messages"] + d["moments"] > 0 for d in days),
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print("WROTE", args.out, "ok=", out["ok"], "over_budget=", over_budget, "same_slot=", same_slot)


if __name__ == "__main__":
    asyncio.run(main())
