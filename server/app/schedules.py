"""主动问候 / 事件书：排班、到期触发、配方。

调度按用户隔离；失败不拖垮其它用户。迟到宽限 3600s，只补最近一次。
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import db
from .security import iso, new_id

GRACE_SECONDS = 3600
STOP_CHASE_RE = re.compile(r"(去睡了|我睡了|睡觉了|去忙了|别烦我|别吵|勿扰)")

RECIPES: Dict[str, Dict[str, Any]] = {
    "greet_sleep": {
        "title": "催睡觉",
        "kind": "greet_sleep",
        "hour": 23,
        "minute": 30,
        "jitter_minutes": 10,
        "daily_limit": 1,
        "require_chatted_today": 1,
        "prompt": (
            "现在是晚上 23:30 左右。用平常聊天的语气催他早点睡："
            "嘴上嫌他又熬夜，心里舍不得；一两句话就好。带一点情绪。"
            "不要说这是自动提醒，不要说自己是 AI。"
            "想配图可写 ⟦图：夜灯下的窗⟧，或发个表情 ⟦表情：嫌弃⟧。"
        ),
    },
    "greet_morning": {
        "title": "叫起床",
        "kind": "greet_morning",
        "hour": 7,
        "minute": 0,
        "jitter_minutes": 10,
        "daily_limit": 1,
        "require_chatted_today": 0,
        "prompt": (
            "现在是早上 7 点。叫他起床：元气满满的早安，嫌他赖床，催他别磨蹭；"
            "一两句话就好。带一点情绪。不要说这是自动提醒，不要说自己是 AI。"
            "想配图可写 ⟦图：晨光里的杯子⟧，或表情 ⟦表情：元气⟧。"
        ),
    },
    # moment_daily 已退役：朋友圈由世界线意图驱动（见 companion/action_dispatcher.py）
}


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_now() -> datetime:
    # 用本机本地时区做「每天 23:30」
    return datetime.now().astimezone()


def compute_next_run(
    *,
    hour: int,
    minute: int,
    jitter_minutes: int = 10,
    after: Optional[datetime] = None,
) -> str:
    """下一次触发：本地 clock ± jitter，且严格晚于 after（默认现在）。"""
    base = after.astimezone() if after else _local_now()
    jitter = random.randint(-max(0, jitter_minutes), max(0, jitter_minutes))
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(minutes=jitter)
    if candidate <= base:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(timezone.utc).isoformat()


def _row_out(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "persona_id": row["persona_id"],
        "kind": row["kind"],
        "title": row["title"] or "",
        "enabled": bool(row["enabled"]),
        "paused": bool(row["paused"]),
        "hour": int(row["hour"]),
        "minute": int(row["minute"]),
        "jitter_minutes": int(row["jitter_minutes"] or 0),
        "min_interval_minutes": int(row["min_interval_minutes"] or 60),
        "daily_limit": int(row["daily_limit"] or 1),
        "prompt": row["prompt"] or "",
        "require_chatted_today": bool(row["require_chatted_today"]),
        "next_run_at": row["next_run_at"],
        "last_run_at": row["last_run_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_schedules(user_id: str, persona_id: str = "") -> List[Dict[str, Any]]:
    if persona_id:
        rows = db.fetchall(
            """
            SELECT * FROM schedules
            WHERE user_id = ? AND persona_id = ?
            ORDER BY hour ASC, minute ASC
            """,
            (user_id, persona_id),
        )
    else:
        rows = db.fetchall(
            """
            SELECT * FROM schedules WHERE user_id = ?
            ORDER BY persona_id, hour ASC, minute ASC
            """,
            (user_id,),
        )
    return [_row_out(r) for r in rows]


def get_schedule(user_id: str, schedule_id: str) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT * FROM schedules WHERE id = ? AND user_id = ?",
        (schedule_id, user_id),
    )
    return _row_out(row) if row else None


def create_schedule(
    *,
    user_id: str,
    persona_id: str,
    kind: str,
    title: str,
    hour: int,
    minute: int,
    prompt: str,
    jitter_minutes: int = 10,
    daily_limit: int = 1,
    require_chatted_today: bool = False,
    min_interval_minutes: int = 60,
    enabled: bool = True,
) -> Dict[str, Any]:
    sid = new_id("sch_")
    now = iso()
    next_at = compute_next_run(hour=hour, minute=minute, jitter_minutes=jitter_minutes)
    db.execute(
        """
        INSERT INTO schedules(
          id, user_id, persona_id, kind, title, enabled, paused,
          hour, minute, jitter_minutes, min_interval_minutes, daily_limit,
          prompt, require_chatted_today, config_json, next_run_at, last_run_at,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, '{}', ?, NULL, ?, ?)
        """,
        (
            sid,
            user_id,
            persona_id,
            kind,
            title,
            1 if enabled else 0,
            hour,
            minute,
            jitter_minutes,
            min_interval_minutes,
            daily_limit,
            prompt,
            1 if require_chatted_today else 0,
            next_at,
            now,
            now,
        ),
    )
    return get_schedule(user_id, sid)  # type: ignore[return-value]


def install_recipe(user_id: str, persona_id: str, recipe_key: str) -> Dict[str, Any]:
    recipe = RECIPES.get(recipe_key)
    if recipe is None:
        raise ValueError(f"未知配方: {recipe_key}")
    # 同人设同 kind 已有则更新
    existing = db.fetchone(
        "SELECT id FROM schedules WHERE user_id = ? AND persona_id = ? AND kind = ?",
        (user_id, persona_id, recipe["kind"]),
    )
    if existing:
        now = iso()
        next_at = compute_next_run(
            hour=int(recipe["hour"]),
            minute=int(recipe["minute"]),
            jitter_minutes=int(recipe["jitter_minutes"]),
        )
        db.execute(
            """
            UPDATE schedules SET title=?, hour=?, minute=?, jitter_minutes=?, daily_limit=?,
              prompt=?, require_chatted_today=?, enabled=1, paused=0, next_run_at=?, updated_at=?
            WHERE id=?
            """,
            (
                recipe["title"],
                recipe["hour"],
                recipe["minute"],
                recipe["jitter_minutes"],
                recipe["daily_limit"],
                recipe["prompt"],
                recipe["require_chatted_today"],
                next_at,
                now,
                existing["id"],
            ),
        )
        return get_schedule(user_id, existing["id"])  # type: ignore[return-value]
    return create_schedule(
        user_id=user_id,
        persona_id=persona_id,
        kind=recipe["kind"],
        title=recipe["title"],
        hour=int(recipe["hour"]),
        minute=int(recipe["minute"]),
        prompt=recipe["prompt"],
        jitter_minutes=int(recipe["jitter_minutes"]),
        daily_limit=int(recipe["daily_limit"]),
        require_chatted_today=bool(recipe["require_chatted_today"]),
    )


def _pause_kinds(user_id: str, persona_id: str, prefix: str) -> None:
    db.execute(
        """
        UPDATE schedules SET paused=1, updated_at=?
        WHERE user_id=? AND persona_id=? AND kind LIKE ? AND enabled=1
        """,
        (iso(), user_id, persona_id, f"{prefix}%"),
    )


def sync_persona_proactive(
    user_id: str,
    persona_id: str,
    *,
    greet: bool,
    moments_on: bool,
) -> None:
    """人设开关语义：greet=允许主动私聊，moments_on=允许发朋友圈。

    固定钟点 greet_morning/greet_sleep 已退役，改由 ProactivePlanner 机会驱动；
    此处一律暂停旧配方，避免定点轰炸。
    """
    _ = greet
    _pause_kinds(user_id, persona_id, "greet_")
    # 朋友圈改由世界线/规划器意图驱动，不再安装 moment_daily
    _pause_kinds(user_id, persona_id, "moment_")
    _ = moments_on  # 开关仍由 ActionDispatcher / ProactiveGates 检查


def patch_schedule(user_id: str, schedule_id: str, **fields) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT * FROM schedules WHERE id = ? AND user_id = ?",
        (schedule_id, user_id),
    )
    if row is None:
        return None
    title = fields.get("title", row["title"])
    enabled = row["enabled"] if fields.get("enabled") is None else (1 if fields["enabled"] else 0)
    paused = row["paused"] if fields.get("paused") is None else (1 if fields["paused"] else 0)
    hour = int(fields.get("hour", row["hour"]))
    minute = int(fields.get("minute", row["minute"]))
    jitter = int(fields.get("jitter_minutes", row["jitter_minutes"]))
    daily_limit = int(fields.get("daily_limit", row["daily_limit"]))
    prompt = fields.get("prompt", row["prompt"])
    require = row["require_chatted_today"] if fields.get("require_chatted_today") is None else (
        1 if fields["require_chatted_today"] else 0
    )
    next_at = row["next_run_at"]
    # 改点钟则重算下次
    if "hour" in fields or "minute" in fields or "jitter_minutes" in fields:
        next_at = compute_next_run(hour=hour, minute=minute, jitter_minutes=jitter)
    if fields.get("reschedule"):
        next_at = compute_next_run(hour=hour, minute=minute, jitter_minutes=jitter)
    now = iso()
    db.execute(
        """
        UPDATE schedules SET title=?, enabled=?, paused=?, hour=?, minute=?, jitter_minutes=?,
          daily_limit=?, prompt=?, require_chatted_today=?, next_run_at=?, updated_at=?
        WHERE id=? AND user_id=?
        """,
        (
            title,
            enabled,
            paused,
            hour,
            minute,
            jitter,
            daily_limit,
            prompt,
            require,
            next_at,
            now,
            schedule_id,
            user_id,
        ),
    )
    return get_schedule(user_id, schedule_id)


def delete_schedule(user_id: str, schedule_id: str) -> bool:
    row = db.fetchone(
        "SELECT id FROM schedules WHERE id = ? AND user_id = ?",
        (schedule_id, user_id),
    )
    if row is None:
        return False
    db.execute("DELETE FROM schedule_runs WHERE schedule_id = ?", (schedule_id,))
    db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
    return True


def _quiet_hours(user_id: str) -> Tuple[str, str]:
    row = db.fetchone("SELECT * FROM user_prefs WHERE user_id = ?", (user_id,))
    start, end = "23:00", "08:00"
    if row is not None:
        try:
            if "quiet_start" in row.keys() and row["quiet_start"]:
                start = row["quiet_start"]
            if "quiet_end" in row.keys() and row["quiet_end"]:
                end = row["quiet_end"]
        except Exception:
            pass
    return start, end


def _in_quiet(user_id: str, when: Optional[datetime] = None) -> bool:
    start_s, end_s = _quiet_hours(user_id)
    try:
        sh, sm = map(int, start_s.split(":")[:2])
        eh, em = map(int, end_s.split(":")[:2])
    except Exception:
        return False
    local = (when or _now()).astimezone()
    mins = local.hour * 60 + local.minute
    a = sh * 60 + sm
    b = eh * 60 + em
    if a == b:
        return False
    if a < b:
        return a <= mins < b
    # 跨午夜：例如 23:00–08:00
    return mins >= a or mins < b


def _runs_today(schedule_id: str) -> int:
    local = _local_now()
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    row = db.fetchone(
        """
        SELECT COUNT(*) AS c FROM schedule_runs
        WHERE schedule_id = ? AND status = 'ok' AND created_at >= ?
        """,
        (schedule_id, day_start),
    )
    return int(row["c"]) if row else 0


def _kind_runs_today(user_id: str, persona_id: str, kind: str) -> int:
    local = _local_now()
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    row = db.fetchone(
        """
        SELECT COUNT(*) AS c FROM schedule_runs r
        JOIN schedules s ON s.id = r.schedule_id
        WHERE s.user_id = ? AND s.persona_id = ? AND s.kind = ?
          AND r.status = 'ok' AND r.created_at >= ?
        """,
        (user_id, persona_id, kind, day_start),
    )
    return int(row["c"]) if row else 0


def _last_user_message_at(user_id: str, conv_id: str) -> Optional[datetime]:
    row = db.fetchone(
        """
        SELECT created_at FROM messages
        WHERE user_id = ? AND conv_id = ? AND mine = 1 AND kind != 'system'
        ORDER BY seq DESC LIMIT 1
        """,
        (user_id, conv_id),
    )
    return _parse_iso(row["created_at"]) if row else None


def _user_chatted_today(user_id: str, conv_id: str) -> bool:
    local = _local_now()
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    row = db.fetchone(
        """
        SELECT id FROM messages
        WHERE user_id = ? AND conv_id = ? AND mine = 1 AND kind != 'system'
          AND created_at >= ?
        LIMIT 1
        """,
        (user_id, conv_id, day_start),
    )
    return row is not None


def _recent_stop_chase(user_id: str, conv_id: str) -> bool:
    """用户说过去睡了/去忙了：当日内不再追。"""
    local = _local_now()
    since_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    since = since_local.astimezone(timezone.utc).isoformat()
    rows = db.fetchall(
        """
        SELECT text FROM messages
        WHERE user_id = ? AND conv_id = ? AND mine = 1 AND kind = 'text'
          AND created_at >= ?
        ORDER BY seq DESC LIMIT 30
        """,
        (user_id, conv_id, since),
    )
    for r in rows:
        if STOP_CHASE_RE.search(r["text"] or ""):
            return True
    return False


def _persona_conv(user_id: str, persona_id: str) -> Optional[str]:
    row = db.fetchone(
        """
        SELECT id FROM conversations
        WHERE user_id = ? AND persona_id = ?
        ORDER BY last_time DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    return row["id"] if row else None


def _record_run(schedule_id: str, user_id: str, status: str, detail: str = "") -> None:
    db.execute(
        """
        INSERT INTO schedule_runs(id, schedule_id, user_id, status, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id("srun_"), schedule_id, user_id, status, detail[:500], iso()),
    )


def _advance_next(row) -> None:
    next_at = compute_next_run(
        hour=int(row["hour"]),
        minute=int(row["minute"]),
        jitter_minutes=int(row["jitter_minutes"] or 0),
    )
    db.execute(
        "UPDATE schedules SET next_run_at = ?, last_run_at = ?, updated_at = ? WHERE id = ?",
        (next_at, iso(), iso(), row["id"]),
    )


def reschedule_on_user_message(user_id: str, persona_id: str) -> None:
    """用户一说话：问候类任务按最短间隔随机推后（不改每日定点配方的 next，只记活跃）。"""
    # 定点配方仍按 clock；此处把「关心」类（若有）重排。定点任务不强制改 next_run_at。
    # 对所有启用问候：若 next 落在 min_interval 内则推后，避免刚聊完立刻催。
    rows = db.fetchall(
        """
        SELECT * FROM schedules
        WHERE user_id = ? AND persona_id = ? AND enabled = 1 AND paused = 0
          AND kind LIKE 'greet_%'
        """,
        (user_id, persona_id),
    )
    now = _now()
    for r in rows:
        nxt = _parse_iso(r["next_run_at"])
        min_i = int(r["min_interval_minutes"] or 60)
        if nxt is None:
            continue
        earliest = now + timedelta(minutes=min_i)
        if nxt < earliest:
            # 推到 earliest ~ earliest+jitter
            jitter = int(r["jitter_minutes"] or 10)
            push = earliest + timedelta(minutes=random.randint(0, max(0, jitter)))
            db.execute(
                "UPDATE schedules SET next_run_at = ?, updated_at = ? WHERE id = ?",
                (push.isoformat(), iso(), r["id"]),
            )


def upcoming_for_persona(user_id: str, persona_id: str, limit: int = 5) -> List[str]:
    """注入提示词用的只读摘要：优先读待发意图，其次旧 schedules。"""
    try:
        from .companion.proactive import upcoming_intents_for_persona

        intents = upcoming_intents_for_persona(user_id, persona_id, limit=limit)
        if intents:
            return intents
    except Exception:
        pass
    rows = db.fetchall(
        """
        SELECT title, next_run_at, hour, minute FROM schedules
        WHERE user_id = ? AND persona_id = ? AND enabled = 1 AND paused = 0
        ORDER BY next_run_at ASC LIMIT ?
        """,
        (user_id, persona_id, limit),
    )
    out: List[str] = []
    for r in rows:
        title = r["title"] or "待办"
        nxt = _parse_iso(r["next_run_at"])
        if nxt:
            local = nxt.astimezone()
            out.append(f"{title}（约 {local.strftime('%m-%d %H:%M')}）")
        else:
            out.append(f"{title}（每天 {int(r['hour']):02d}:{int(r['minute']):02d}）")
    return out


def due_schedules(now: Optional[datetime] = None) -> List[Any]:
    now = now or _now()
    # 只取 next_run_at <= now；过期超过 grace 的仍取出，由 tick 决定只补一次
    # 尊重 user_prefs.pause_proactive：用户全局暂停时不再触发
    return db.fetchall(
        """
        SELECT s.* FROM schedules s
        LEFT JOIN user_prefs p ON p.user_id = s.user_id
        WHERE s.enabled = 1 AND s.paused = 0
          AND s.next_run_at IS NOT NULL AND s.next_run_at <= ?
          AND COALESCE(p.pause_proactive, 0) = 0
        ORDER BY s.next_run_at ASC
        LIMIT 50
        """,
        (now.isoformat(),),
    )


def pause_all_for_user(user_id: str, *, paused: bool = True) -> int:
    """一键暂停/恢复该用户全部定时任务。"""
    from .security import iso as _iso

    before = db.fetchall(
        "SELECT id FROM schedules WHERE user_id = ? AND enabled = 1",
        (user_id,),
    )
    db.execute(
        "UPDATE schedules SET paused = ?, updated_at = ? WHERE user_id = ? AND enabled = 1",
        (1 if paused else 0, _iso(), user_id),
    )
    return len(before)


def evaluate_skip(row, *, conv_id: str) -> Optional[str]:
    """返回跳过原因，或 None 表示可发。"""
    user_id = row["user_id"]
    if _in_quiet(user_id):
        return "免打扰时段"
    if _runs_today(row["id"]) >= int(row["daily_limit"] or 1):
        return "已达每日上限"
    if _kind_runs_today(user_id, row["persona_id"], row["kind"]) >= max(2, int(row["daily_limit"] or 1)):
        return "同类催促今日已达上限"
    if _recent_stop_chase(user_id, conv_id):
        return "用户说了去睡/去忙，不再追"
    if row["require_chatted_today"] and not _user_chatted_today(user_id, conv_id):
        return "今天还没聊过，跳过"
    last = _last_user_message_at(user_id, conv_id)
    if last and (_now() - last).total_seconds() < 300:
        # 极短间隔内刚聊过：稍后再说（停追已优先判断）
        return "刚聊过，稍后再说"
    return None


async def fire_schedule(row) -> str:
    """执行一条到期任务。返回 ok / skipped / error。"""
    from .routers.chat import _generate_reply  # 避免循环在模块顶导入

    user_id = row["user_id"]
    persona_id = row["persona_id"]
    kind = row["kind"] or ""
    conv_id = _persona_conv(user_id, persona_id)

    due_at = _parse_iso(row["next_run_at"])
    if due_at and (_now() - due_at).total_seconds() > GRACE_SECONDS * 24:
        _record_run(row["id"], user_id, "skipped", "停机过久，跳过补发")
        _advance_next(row)
        return "skipped"

    if kind.startswith("moment"):
        # 阶段4：旧 moment_* 配方停用，改走世界线意图；直接跳过并推进以免重试风暴
        _record_run(row["id"], user_id, "skipped", "moment_daily_retired_use_worldline_intents")
        _advance_next(row)
        # 顺带暂停，避免反复到期
        try:
            db.execute(
                "UPDATE schedules SET paused=1, updated_at=? WHERE id=?",
                (iso(), row["id"]),
            )
        except Exception:
            pass
        return "skipped"

    if not conv_id:
        _record_run(row["id"], user_id, "skipped", "无人设会话")
        _advance_next(row)
        return "skipped"

    reason = evaluate_skip(row, conv_id=conv_id)
    if reason:
        _record_run(row["id"], user_id, "skipped", reason)
        _advance_next(row)
        return "skipped"

    prompt = (row["prompt"] or "").strip()
    if not prompt:
        _record_run(row["id"], user_id, "skipped", "无提示词")
        _advance_next(row)
        return "skipped"

    try:
        await _generate_reply(
            user_id=user_id,
            conv_id=conv_id,
            persona_id=persona_id,
            user_text=prompt,
            client="system",
            internal=True,
        )
        # M4：主动问候后打开跟随窗（listen），默认 5 分钟
        try:
            minutes = 5
            try:
                minutes = int(row["listen_minutes"] or 5)
            except Exception:
                minutes = 5
            minutes = max(1, min(60, minutes))
            from datetime import datetime, timedelta, timezone

            until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
            db.execute(
                "UPDATE conversations SET listen_until = ? WHERE id = ? AND user_id = ?",
                (until, conv_id, user_id),
            )
        except Exception:
            pass
        _record_run(row["id"], user_id, "ok", "")
        _advance_next(row)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        _record_run(row["id"], user_id, "error", str(exc))
        _advance_next(row)
        return "error"


async def _complete_once(user_id: str, persona_id: str, prompt: str) -> str:
    """非流式短补全，用于朋友圈文案。只走 DSH，无渠道回退。"""
    from .dsh_gateway import dsh_complete, require_dsh
    from .security import new_id

    require_dsh()
    persona = db.fetchone(
        "SELECT * FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if persona is None:
        return ""
    bio = (persona["bio"] or "").strip()
    name = persona["name"] or "角色"
    system = (
        f"你是{name}。{bio}\n"
        "现在只写一条朋友圈正文。只输出正文，不要解释、不要提问、不要给备选。"
    )
    moment_user = (prompt or "").strip()
    # 每次新会话，避免粘住上一次「帮我改文案」语境
    return await dsh_complete(
        user_id=user_id,
        conversation_id=f"sys-moment-{persona_id}-{new_id('m')[:10]}",
        text=moment_user,
        system=system,
    )


def list_runs(user_id: str, schedule_id: str = "", limit: int = 30) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 100))
    if schedule_id:
        rows = db.fetchall(
            """
            SELECT * FROM schedule_runs
            WHERE user_id = ? AND schedule_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, schedule_id, limit),
        )
    else:
        rows = db.fetchall(
            """
            SELECT * FROM schedule_runs WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, limit),
        )
    return [
        {
            "id": r["id"],
            "schedule_id": r["schedule_id"],
            "status": r["status"],
            "detail": r["detail"] or "",
            "created_at": r["created_at"],
        }
        for r in rows
    ]
