"""用户显式提醒 / 每日任务：从用户话或 AI 暗号解析，写入 schedules。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import schedules as sched

# AI 暗号
_TAG_DAILY = re.compile(
    r"⟦提醒[:：]\s*每天\s*(\d{1,2})\s*[:：点时]\s*(\d{0,2})\s*(.+?)⟧|"
    r"\[\[提醒[:：]\s*每天\s*(\d{1,2})\s*[:：点时]\s*(\d{0,2})\s*(.+?)\]\]",
    re.S,
)
_TAG_ONCE_REL = re.compile(
    r"⟦提醒[:：]\s*一次\s*\+(\d+)\s*m\s+(.+?)⟧|"
    r"\[\[提醒[:：]\s*一次\s*\+(\d+)\s*m\s+(.+?)\]\]",
    re.S,
)
_TAG_ONCE_HM = re.compile(
    r"⟦提醒[:：]\s*一次\s*(\d{1,2})\s*[:：点时]\s*(\d{0,2})\s*(.+?)⟧|"
    r"\[\[提醒[:：]\s*一次\s*(\d{1,2})\s*[:：点时]\s*(\d{0,2})\s*(.+?)\]\]",
    re.S,
)
_TAG_CANCEL = re.compile(
    r"⟦取消提醒[:：]\s*(.+?)⟧|\[\[取消提醒[:：]\s*(.+?)\]\]",
    re.S,
)
_TAG_LIST = re.compile(r"⟦任务列表⟧|\[\[任务列表\]\]")

# 用户口语
_USER_DAILY = re.compile(
    r"每天\s*(?:早上|上午|中午|下午|晚上|夜里)?\s*(\d{1,2})\s*(?:[:：点时])\s*(\d{0,2})?\s*(?:分)?\s*(?:提醒我|叫我|叫一下|跟我说|和我说)?\s*(.*)$",
    re.I,
)
_USER_IN_HOURS = re.compile(
    r"(?:过)?\s*(\d+|半|一|两|二|三|四|五|六|七|八|九|十)\s*(?:个)?小时后\s*(?:提醒我|叫我|跟我说|和我说)?\s*(.*)$",
    re.I,
)
_USER_IN_MINS = re.compile(
    r"(?:过)?\s*(\d+|半|一|两|二|三|四|五|六|七|八|九|十)\s*分钟后\s*(?:提醒我|叫我|跟我说|和我说)?\s*(.*)$",
    re.I,
)


def _cn_int(raw: str) -> int:
    s = (raw or "").strip()
    if s.isdigit():
        return int(s)
    table = {
        "一": 1,
        "两": 2,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    return table.get(s, 1)
_USER_SOON = re.compile(
    r"(?:待会|待一会儿|一会儿|一会|稍后)\s*(?:提醒我|叫我|跟我说|和我说)?\s*(.*)$",
    re.I,
)
_USER_CANCEL = re.compile(r"(?:取消|删掉|删除)\s*(?:一下)?\s*(?:提醒|定时|任务)\s*(.*)$", re.I)
_USER_LIST = re.compile(r"(?:现在)?有什么(?:提醒|任务|定时)|看看(?:提醒|任务)|列出(?:提醒|任务)", re.I)


def _speak_prompt(body: str) -> str:
    tip = (body or "提醒一下").strip()[:120]
    return (
        f"到点了，用平常聊天语气提醒对方：{tip}。"
        "一两句话就好，带一点情绪；不要说这是定时任务或自动提醒，不要说自己是 AI。"
    )


def _hm(h: str, m: str) -> Tuple[int, int]:
    hour = max(0, min(23, int(h or 0)))
    minute = max(0, min(59, int(m or 0) if (m or "").strip() != "" else 0))
    return hour, minute


def extract_reminder_tags(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """从 AI 回复抽出提醒暗号，返回 (净文本, intents)。"""
    intents: List[Dict[str, Any]] = []
    out = text or ""

    def daily(m: re.Match) -> str:
        h = m.group(1) or m.group(4)
        mi = m.group(2) or m.group(5)
        body = (m.group(3) or m.group(6) or "").strip()
        hour, minute = _hm(h, mi)
        intents.append(
            {
                "op": "create",
                "mode": "daily",
                "hour": hour,
                "minute": minute,
                "title": body[:40] or f"每天{hour:02d}:{minute:02d}",
                "body": body or f"每天{hour:02d}:{minute:02d}提醒",
            }
        )
        return ""

    def once_rel(m: re.Match) -> str:
        mins = int(m.group(1) or m.group(3) or 0)
        body = (m.group(2) or m.group(4) or "").strip()
        intents.append(
            {
                "op": "create",
                "mode": "once",
                "in_minutes": max(1, mins),
                "title": body[:40] or f"{mins}分钟后",
                "body": body or "提醒一下",
            }
        )
        return ""

    def once_hm(m: re.Match) -> str:
        h = m.group(1) or m.group(4)
        mi = m.group(2) or m.group(5)
        body = (m.group(3) or m.group(6) or "").strip()
        hour, minute = _hm(h, mi)
        intents.append(
            {
                "op": "create",
                "mode": "once",
                "hour": hour,
                "minute": minute,
                "title": body[:40] or f"今天{hour:02d}:{minute:02d}",
                "body": body or "提醒一下",
            }
        )
        return ""

    def cancel(m: re.Match) -> str:
        q = (m.group(1) or m.group(2) or "").strip()
        intents.append({"op": "cancel", "query": q})
        return ""

    def listing(_: re.Match) -> str:
        intents.append({"op": "list"})
        return ""

    out = _TAG_DAILY.sub(daily, out)
    out = _TAG_ONCE_REL.sub(once_rel, out)
    out = _TAG_ONCE_HM.sub(once_hm, out)
    out = _TAG_CANCEL.sub(cancel, out)
    out = _TAG_LIST.sub(listing, out)
    return out.strip(), intents


def parse_user_reminder_intents(text: str) -> List[Dict[str, Any]]:
    """从用户原话解析提醒意图（不依赖模型暗号）。"""
    raw = (text or "").strip()
    if not raw:
        return []
    # 去掉媒体暗号干扰
    line = re.sub(r"⟦.*?⟧|\[\[.*?\]\]", "", raw).strip()
    intents: List[Dict[str, Any]] = []

    if _USER_LIST.search(line):
        intents.append({"op": "list"})
        return intents

    m = _USER_CANCEL.search(line)
    if m:
        intents.append({"op": "cancel", "query": (m.group(1) or "").strip()})
        return intents

    m = _USER_DAILY.search(line)
    if m:
        hour, minute = _hm(m.group(1), m.group(2) or "0")
        body = (m.group(3) or "").strip() or "提醒一下"
        intents.append(
            {
                "op": "create",
                "mode": "daily",
                "hour": hour,
                "minute": minute,
                "title": body[:40],
                "body": body,
            }
        )
        return intents

    m = _USER_IN_HOURS.search(line)
    if m:
        raw_n = (m.group(1) or "").strip()
        if raw_n == "半":
            in_minutes = 30
        else:
            in_minutes = max(1, _cn_int(raw_n)) * 60
        body = (m.group(2) or "").strip() or "提醒一下"
        intents.append(
            {
                "op": "create",
                "mode": "once",
                "in_minutes": in_minutes,
                "title": body[:40],
                "body": body,
            }
        )
        return intents

    m = _USER_IN_MINS.search(line)
    if m:
        raw_n = (m.group(1) or "").strip()
        mins = 30 if raw_n == "半" else max(1, _cn_int(raw_n))
        body = (m.group(2) or "").strip() or "提醒一下"
        intents.append(
            {
                "op": "create",
                "mode": "once",
                "in_minutes": mins,
                "title": body[:40],
                "body": body,
            }
        )
        return intents

    m = _USER_SOON.search(line)
    if m:
        body = (m.group(1) or "").strip() or "提醒一下"
        intents.append(
            {
                "op": "create",
                "mode": "once",
                "in_minutes": 15,
                "title": body[:40],
                "body": body,
            }
        )
        return intents

    return intents


def format_task_list(user_id: str, persona_id: str) -> str:
    rows = [
        s
        for s in sched.list_schedules(user_id, persona_id)
        if s.get("enabled") and not s.get("paused") and str(s.get("kind") or "").startswith("user_")
    ]
    if not rows:
        return "现在没有待办提醒。"
    lines = []
    for s in rows[:20]:
        kind = s.get("kind") or ""
        title = s.get("title") or "提醒"
        nxt = s.get("next_run_at") or ""
        when = ""
        if nxt:
            try:
                dt = datetime.fromisoformat(nxt.replace("Z", "+00:00")).astimezone()
                when = dt.strftime("%m-%d %H:%M")
            except Exception:
                when = nxt
        if kind == "user_daily":
            lines.append(f"· 每天 {int(s['hour']):02d}:{int(s['minute']):02d}「{title}」下次约 {when}")
        else:
            lines.append(f"· 一次「{title}」约 {when}")
    return "当前提醒：\n" + "\n".join(lines)


def apply_reminder_intents(
    *,
    user_id: str,
    persona_id: str,
    intents: List[Dict[str, Any]],
) -> List[str]:
    """执行意图，返回给人看/给模型的短句结果。"""
    notes: List[str] = []
    for it in intents or []:
        op = it.get("op")
        if op == "list":
            notes.append(format_task_list(user_id, persona_id))
            continue
        if op == "cancel":
            q = (it.get("query") or "").strip()
            deleted = sched.delete_user_reminders(user_id=user_id, persona_id=persona_id, query=q)
            if deleted:
                notes.append(f"已取消 {deleted} 条提醒" + (f"（匹配「{q}」）" if q else ""))
            else:
                notes.append("没找到要取消的提醒" + (f"（匹配「{q}」）" if q else ""))
            continue
        if op != "create":
            continue
        mode = it.get("mode") or "once"
        title = (it.get("title") or "提醒").strip()[:64]
        body = (it.get("body") or title).strip()[:200]
        prompt = _speak_prompt(body)
        if mode == "daily":
            hour = int(it.get("hour") or 0)
            minute = int(it.get("minute") or 0)
            item = sched.create_schedule(
                user_id=user_id,
                persona_id=persona_id,
                kind="user_daily",
                title=title,
                hour=hour,
                minute=minute,
                prompt=prompt,
                jitter_minutes=0,
                daily_limit=1,
                require_chatted_today=False,
                min_interval_minutes=0,
            )
            notes.append(f"已记下每天 {hour:02d}:{minute:02d} 提醒「{title}」")
            _ = item
            continue
        # once
        in_minutes = it.get("in_minutes")
        local = datetime.now().astimezone()
        if in_minutes is not None:
            run_at = local + timedelta(minutes=int(in_minutes))
        else:
            hour = int(it.get("hour") or local.hour)
            minute = int(it.get("minute") or 0)
            run_at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_at <= local:
                run_at = run_at + timedelta(days=1)
        item = sched.create_schedule(
            user_id=user_id,
            persona_id=persona_id,
            kind="user_once",
            title=title,
            hour=run_at.hour,
            minute=run_at.minute,
            prompt=prompt,
            jitter_minutes=0,
            daily_limit=1,
            require_chatted_today=False,
            min_interval_minutes=0,
            next_run_at=run_at.astimezone(timezone.utc).isoformat(),
        )
        notes.append(f"已记下 {run_at.strftime('%m-%d %H:%M')} 提醒「{title}」")
        _ = item
    return notes
