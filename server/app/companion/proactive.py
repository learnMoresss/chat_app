"""机会驱动的主动私聊 / 朋友圈规划：候选 → 打分 → 门禁 → action_intents。"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .. import db
from ..security import iso
from .affect import AffectService
from .intent_service import IntentService
from .worldline import get_world_state, worldline_enabled

# ---- 预算 ----
MSG_DAILY_CAP = 4
MSG_DAILY_FLOOR = 2
MSG_MIN_GAP_MIN = 90
MOMENT_DAILY_CAP = 2
RECENT_CHAT_SKIP_MIN = 20
LONELY_HOURS = 6
WORRY_THRESHOLD = 55
SADNESS_THRESHOLD = 55

# 聊天跟进：时间词 + 事件词
_TIME_RE = re.compile(
    r"(明天|后天|大后天|今晚|今晚|周末|下周[一二三四五六日天]?|"
    r"这周[一二三四五六日天]?|下个月|周一|周二|周三|周四|周五|周六|周日|"
    r"\d{1,2}\s*[点时:]|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?)"
)
_EVENT_RE = re.compile(
    r"(面试|二面|终面|体检|考试|答辩|生日|见面|约会|出差|起飞|登机|"
    r"手术|复诊|搬家|入职|离职|开会|汇报|答辩|比赛|演出)"
)
_DONE_RE = re.compile(r"(面完|考完|结束了|搞定了|结束啦|过了|没过|挂了|取消了|改期)")


@dataclass
class Candidate:
    kind: str  # message | moment
    source: str  # world | chat
    reason: str
    score: float
    context: Dict[str, Any] = field(default_factory=dict)
    not_before: str = ""
    expires_at: str = ""
    idempotency_key: str = ""
    topic: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except Exception:
        return None


def _persona_flags(user_id: str, persona_id: str) -> Dict[str, bool]:
    row = db.fetchone(
        "SELECT greet, moments_on FROM personas WHERE id=? AND user_id=?",
        (persona_id, user_id),
    )
    if not row:
        return {"greet": False, "moments_on": False}
    return {"greet": bool(row["greet"]), "moments_on": bool(row["moments_on"])}


def _pause_proactive(user_id: str) -> bool:
    row = db.fetchone("SELECT pause_proactive FROM user_prefs WHERE user_id=?", (user_id,))
    return bool(row and row["pause_proactive"])


def _persona_conv(user_id: str, persona_id: str) -> Optional[str]:
    row = db.fetchone(
        """
        SELECT id FROM conversations
        WHERE user_id=? AND persona_id=?
        ORDER BY last_time DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    return row["id"] if row else None


def _last_user_msg_at(user_id: str, conv_id: str) -> Optional[datetime]:
    row = db.fetchone(
        """
        SELECT created_at FROM messages
        WHERE user_id=? AND conv_id=? AND mine=1 AND kind='text'
        ORDER BY seq DESC LIMIT 1
        """,
        (user_id, conv_id),
    )
    return _parse_ts(row["created_at"]) if row else None


def _user_chatted_today(user_id: str, conv_id: str) -> bool:
    local = _local_now()
    since_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    since = since_local.astimezone(timezone.utc).isoformat()
    row = db.fetchone(
        """
        SELECT 1 FROM messages
        WHERE user_id=? AND conv_id=? AND mine=1 AND kind='text' AND created_at>=?
        LIMIT 1
        """,
        (user_id, conv_id, since),
    )
    return bool(row)


def _hour_active_recently(user_id: str, conv_id: str, hour: int, *, days: int = 5) -> bool:
    """近几天该本地小时是否有用户发言。"""
    since = (_now() - timedelta(days=days)).isoformat()
    rows = db.fetchall(
        """
        SELECT created_at FROM messages
        WHERE user_id=? AND conv_id=? AND mine=1 AND kind='text' AND created_at>=?
        ORDER BY seq DESC LIMIT 80
        """,
        (user_id, conv_id, since),
    )
    for r in rows:
        t = _parse_ts(r["created_at"])
        if t and t.astimezone().hour == hour:
            return True
    return False


def _done_intents_today(user_id: str, persona_id: str, action_kind: str) -> List[Dict[str, Any]]:
    local = _local_now()
    since_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    since = since_local.astimezone(timezone.utc).isoformat()
    rows = db.fetchall(
        """
        SELECT * FROM action_intents
        WHERE user_id=? AND persona_id=? AND action_kind=? AND status='done'
          AND updated_at>=?
        ORDER BY updated_at ASC
        """,
        (user_id, persona_id, action_kind, since),
    )
    return [dict(r) for r in rows]


def _unanswered_streak(user_id: str, persona_id: str) -> int:
    """最近连续几条主动消息用户未回。"""
    conv = _persona_conv(user_id, persona_id)
    if not conv:
        return 0
    # 看最近几条 bot 主动（internal 看不出来，用 action_intents done message）
    rows = db.fetchall(
        """
        SELECT updated_at FROM action_intents
        WHERE user_id=? AND persona_id=? AND action_kind='message' AND status='done'
        ORDER BY updated_at DESC LIMIT 5
        """,
        (user_id, persona_id),
    )
    streak = 0
    for r in rows:
        sent_at = r["updated_at"]
        replied = db.fetchone(
            """
            SELECT 1 FROM messages
            WHERE user_id=? AND conv_id=? AND mine=1 AND kind='text'
              AND created_at > ?
            LIMIT 1
            """,
            (user_id, conv, sent_at),
        )
        if replied:
            break
        streak += 1
    return streak


def _day_plan_bounds(user_id: str, persona_id: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """日计划第一个/最后一个活动起止。"""
    today = date.today().isoformat()
    plan = db.fetchone(
        """
        SELECT id FROM world_plans
        WHERE user_id=? AND persona_id=? AND plan_date=?
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id, today),
    )
    if plan:
        rows = db.fetchall(
            """
            SELECT starts_at, ends_at FROM world_activities
            WHERE user_id=? AND persona_id=? AND plan_id=?
              AND status IN ('planned','running','completed')
              AND starts_at!=''
            ORDER BY starts_at ASC
            """,
            (user_id, persona_id, plan["id"]),
        )
    else:
        rows = db.fetchall(
            """
            SELECT starts_at, ends_at FROM world_activities
            WHERE user_id=? AND persona_id=?
              AND status IN ('planned','running','completed')
              AND starts_at!='' AND starts_at LIKE ?
            ORDER BY starts_at ASC
            """,
            (user_id, persona_id, today + "%"),
        )
    if not rows:
        return None, None
    first = _parse_ts(rows[0]["starts_at"])
    last_end = None
    for r in rows:
        e = _parse_ts(r["ends_at"] or r["starts_at"])
        if e and (last_end is None or e > last_end):
            last_end = e
    return first, last_end


def _window_random(center: datetime, *, before_min: int, after_min: int, ttl_hours: float) -> Tuple[str, str]:
    offset = random.randint(-before_min, after_min)
    nb = center + timedelta(minutes=offset)
    if nb < _now():
        nb = _now() + timedelta(minutes=random.randint(5, 25))
    exp = nb + timedelta(hours=ttl_hours)
    return nb.isoformat(), exp.isoformat()


def _recent_world_events(user_id: str, persona_id: str, *, hours: float = 3.0) -> List[Dict[str, Any]]:
    since = (_now() - timedelta(hours=hours)).isoformat()
    rows = db.fetchall(
        """
        SELECT id, kind, payload_json, created_at FROM companion_events
        WHERE user_id=? AND persona_id=? AND source='world'
          AND kind IN ('activity_complete','disturbance','weather_change')
          AND created_at>=?
        ORDER BY created_at DESC LIMIT 12
        """,
        (user_id, persona_id, since),
    )
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {}
        out.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "payload": payload,
                "created_at": r["created_at"],
            }
        )
    return out


# ---- 门禁 ----


class ProactiveGates:
    """预算与打扰门禁；规划与执行前复检共用。"""

    def budget_status(self, user_id: str, persona_id: str) -> Dict[str, Any]:
        msgs = _done_intents_today(user_id, persona_id, "message")
        moments = _done_intents_today(user_id, persona_id, "moment")
        streak = _unanswered_streak(user_id, persona_id)
        msg_cap = MSG_DAILY_CAP
        if streak >= 2:
            msg_cap = max(MSG_DAILY_FLOOR, MSG_DAILY_CAP // 2)
        last_msg_at = msgs[-1]["updated_at"] if msgs else ""
        return {
            "messages_sent": len(msgs),
            "moments_sent": len(moments),
            "message_cap": msg_cap,
            "moment_cap": MOMENT_DAILY_CAP,
            "message_left": max(0, msg_cap - len(msgs)),
            "moment_left": max(0, MOMENT_DAILY_CAP - len(moments)),
            "unanswered_streak": streak,
            "last_message_at": last_msg_at,
            "min_gap_min": MSG_MIN_GAP_MIN,
        }

    def check(
        self,
        user_id: str,
        persona_id: str,
        *,
        action_kind: str,
        for_execute: bool = False,
        not_before: str = "",
    ) -> Optional[str]:
        """返回拦截原因，或 None 表示可通过。

        规划阶段：未来窗口（not_before 距今超过刚聊过阈值）可排队，不因「刚聊过」拦截。
        执行阶段：完整门禁。
        """
        if _pause_proactive(user_id):
            return "全局暂停主动"
        flags = _persona_flags(user_id, persona_id)
        if action_kind == "message" and not flags["greet"]:
            return "greet_off"
        if action_kind == "moment" and not flags["moments_on"]:
            return "moments_off"

        conv = _persona_conv(user_id, persona_id)
        if not conv:
            return "无人设会话"

        # 停追 / 免打扰 / 刚聊过：执行必查；规划仅对「即将到点」的候选查
        nb = _parse_ts(not_before) if not_before else None
        imminent = for_execute or (nb is None) or ((nb - _now()).total_seconds() <= RECENT_CHAT_SKIP_MIN * 60)

        if imminent:
            try:
                from ..schedules import _in_quiet

                if _in_quiet(user_id):
                    return "免打扰时段"
            except Exception:
                pass
            try:
                from ..schedules import _recent_stop_chase

                if _recent_stop_chase(user_id, conv):
                    return "用户说了去睡/去忙"
            except Exception:
                pass
            last_u = _last_user_msg_at(user_id, conv)
            if last_u and (_now() - last_u).total_seconds() < RECENT_CHAT_SKIP_MIN * 60:
                return "刚聊过"

        bud = self.budget_status(user_id, persona_id)
        if action_kind == "message":
            if bud["message_left"] <= 0:
                return "私聊日预算用尽"
            # 间隔只在执行或即将到点时卡
            if imminent and bud["last_message_at"]:
                t = _parse_ts(bud["last_message_at"])
                if t and (_now() - t).total_seconds() < MSG_MIN_GAP_MIN * 60:
                    return "间隔不足90分钟"
        if action_kind == "moment":
            if bud["moment_left"] <= 0:
                return "朋友圈日预算用尽"

        return None


# ---- 聊天跟进识别 ----


def extract_followup_topics(user_text: str) -> List[Dict[str, Any]]:
    t = (user_text or "").strip()
    if not t:
        return []
    if not _TIME_RE.search(t) or not _EVENT_RE.search(t):
        return []
    time_m = _TIME_RE.search(t)
    event_m = _EVENT_RE.search(t)
    topic = f"{(time_m.group(0) if time_m else '').strip()}{(event_m.group(0) if event_m else '').strip()}"
    event = event_m.group(0) if event_m else "事"
    return [{"topic": topic[:40], "event": event, "snippet": t[:120]}]


def _event_aliases(event: str) -> List[str]:
    e = (event or "").strip()
    aliases = [e] if e else []
    if e in ("面试", "二面", "终面"):
        aliases.extend(["面试", "二面", "终面", "面完", "过了", "挂了"])
    if e == "生日":
        aliases.extend(["生日", "礼物", "过生"])
    if e in ("体检", "考试", "答辩"):
        aliases.extend([e, f"{e}完", "考完", "结束"])
    return [a for a in aliases if a]


def cancel_matching_followups(user_id: str, persona_id: str, user_text: str) -> int:
    """用户提起已完结/同一话题时取消待发跟进。"""
    t = (user_text or "").strip()
    if not t:
        return 0
    done_hint = bool(_DONE_RE.search(t))
    svc = IntentService()
    open_rows = svc.list_open(user_id, persona_id, limit=40)
    n = 0
    for row in open_rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except Exception:
            payload = {}
        if (payload.get("candidate_type") or "") != "chat_followup":
            continue
        event = payload.get("event") or ""
        aliases = _event_aliases(event)
        hit = any(a and a in t for a in aliases)
        # 「面完了」这类完结语：只要跟进事件是面试族也取消
        if done_hint and event in ("面试", "二面", "终面") and any(k in t for k in ("面", "过了", "挂了", "改期")):
            hit = True
        if hit:
            svc.cancel(user_id, persona_id, row["id"], reason="user_mentioned")
            n += 1
    return n


def _followup_reasons(event: str) -> Tuple[str, str]:
    """返回 (cheer_reason, after_reason)。"""
    if event == "生日":
        return (
            "惦记你提到的生日，想提醒你礼物别拖到最后",
            "想问问生日礼物准备得怎么样了",
        )
    if event in ("面试", "二面", "终面"):
        return (
            f"惦记你提到的{event}，想提前说句打气的话",
            f"想问问你{event}结果怎么样了",
        )
    return (
        f"惦记你提到的{event}，想提前关心一句",
        f"想问问你{event}怎么样了",
    )


def schedule_chat_followups(user_id: str, persona_id: str, user_text: str) -> List[Dict[str, Any]]:
    """从用户原话生成 followup 意图（事件前打气 / 事后关心）。"""
    cancel_matching_followups(user_id, persona_id, user_text)
    topics = extract_followup_topics(user_text)
    if not topics:
        return []
    flags = _persona_flags(user_id, persona_id)
    if not flags["greet"]:
        return []
    created = []
    svc = IntentService()
    now = _now()
    for item in topics:
        topic = item["topic"]
        event = item["event"]
        cheer_reason, after_reason = _followup_reasons(event)
        # 宽窗口：今晚~明天夜里打气；事件后 1~3h 问结果（不编精确日期）
        cheer_nb = now + timedelta(hours=random.uniform(4, 14))
        cheer_exp = cheer_nb + timedelta(hours=8)
        after_nb = now + timedelta(hours=random.uniform(20, 36))
        after_exp = after_nb + timedelta(hours=12)
        for phase, nb, exp, reason, score in (
            (
                "cheer",
                cheer_nb,
                cheer_exp,
                cheer_reason,
                72.0,
            ),
            (
                "after",
                after_nb,
                after_exp,
                after_reason,
                68.0,
            ),
        ):
            key = f"followup:{user_id}:{persona_id}:{topic}:{phase}:{date.today().isoformat()}"
            gate = ProactiveGates().check(user_id, persona_id, action_kind="message")
            # 跟进允许先排队，门禁在执行时再卡；规划阶段只拦硬开关
            if gate in ("全局暂停主动", "greet_off", "无人设会话"):
                continue
            row = svc.create(
                user_id=user_id,
                persona_id=persona_id,
                action_kind="message",
                reason_summary=reason,
                source="chat",
                payload={
                    "candidate_type": "chat_followup",
                    "phase": phase,
                    "topic": topic,
                    "event": event,
                    "snippet": item.get("snippet") or "",
                    "score": score,
                    "context_points": [item.get("snippet") or topic],
                },
                not_before=nb.isoformat(),
                expires_at=exp.isoformat(),
                status="ready" if worldline_enabled(user_id, persona_id) or True else "pending",
                idempotency_key=key,
            )
            created.append(row)
    return created


# ---- 规划器 ----


class ProactivePlanner:
    def __init__(self) -> None:
        self.gates = ProactiveGates()
        self.intents = IntentService()
        self._skipped: List[Dict[str, str]] = []

    def plan(self, user_id: str, persona_id: str, *, trigger: str = "tick") -> Dict[str, Any]:
        self._skipped = []
        flags = _persona_flags(user_id, persona_id)
        if not flags["greet"] and not flags["moments_on"]:
            return {"accepted": [], "skipped": [{"reason": "开关全关"}], "budget": self.gates.budget_status(user_id, persona_id)}

        candidates: List[Candidate] = []
        candidates.extend(self._routine_windows(user_id, persona_id))
        candidates.extend(self._world_experiences(user_id, persona_id))
        candidates.extend(self._mood_reachout(user_id, persona_id))
        candidates.extend(self._lonely(user_id, persona_id))

        # 心情系数
        affect = AffectService().get_or_init(user_id, persona_id)
        for c in candidates:
            c.score = self._score_with_mood(c, affect) * random.uniform(0.85, 1.15)

        candidates.sort(key=lambda x: x.score, reverse=True)
        accepted: List[Dict[str, Any]] = []
        for c in candidates:
            if c.kind == "message" and not flags["greet"]:
                self._skipped.append({"reason": "greet_off", "type": c.context.get("candidate_type", "")})
                continue
            if c.kind == "moment" and not flags["moments_on"]:
                self._skipped.append({"reason": "moments_off", "type": c.context.get("candidate_type", "")})
                continue
            if c.kind != "message":
                continue
            reason = self.gates.check(
                user_id, persona_id, action_kind=c.kind, not_before=c.not_before
            )
            if reason:
                self._skipped.append({"reason": reason, "type": c.context.get("candidate_type", c.reason[:20])})
                continue
            if c.score < 40:
                self._skipped.append({"reason": "分数过低", "type": c.context.get("candidate_type", "")})
                continue
            row = self._accept(user_id, persona_id, c)
            if row:
                accepted.append(row)
                break  # 每次 plan 最多收 1 条私聊
        # 再扫一遍 moment
        for c in candidates:
            if c.kind != "moment":
                continue
            if any(a.get("action_kind") == "moment" for a in accepted):
                break
            if not flags["moments_on"]:
                break
            reason = self.gates.check(
                user_id, persona_id, action_kind="moment", not_before=c.not_before
            )
            if reason:
                self._skipped.append({"reason": reason, "type": "moment"})
                continue
            if c.score < 45:
                continue
            row = self._accept(user_id, persona_id, c)
            if row:
                accepted.append(row)

        # 持久化最近一次规划摘要（看板用）
        self._store_plan_log(user_id, persona_id, trigger, accepted)
        return {
            "accepted": accepted,
            "skipped": self._skipped[:20],
            "budget": self.gates.budget_status(user_id, persona_id),
            "trigger": trigger,
        }

    def _score_with_mood(self, c: Candidate, affect: Dict[str, Any]) -> float:
        s = c.score
        joy = int(affect.get("joy") or 35)
        worry = int(affect.get("worry") or 35)
        sadness = int(affect.get("sadness") or 35)
        annoyance = int(affect.get("annoyance") or 35)
        ctype = c.context.get("candidate_type") or ""
        if ctype == "mood_reachout":
            s += (worry + sadness - 70) * 0.4
        if ctype == "routine_wake" and joy > 50:
            s += 5
        if ctype == "world_share" and joy > 45:
            s += 8
        if annoyance > 60 and ctype == "lonely":
            s -= 15
        if c.kind == "moment" and sadness > 60:
            s -= 10  # 低落时少发朋友圈
        return s

    def _accept(self, user_id: str, persona_id: str, c: Candidate) -> Optional[Dict[str, Any]]:
        # 同人设只保留一条待发私聊：新分更高才替换
        if c.kind == "message":
            open_msgs = [
                r
                for r in self.intents.list_open(user_id, persona_id, limit=30)
                if (r.get("action_kind") or "") == "message"
                and (r.get("status") or "") in ("pending", "ready")
            ]
            for old in open_msgs:
                try:
                    old_payload = json.loads(old.get("payload_json") or "{}")
                except Exception:
                    old_payload = {}
                # 保留 chat_followup，不轻易覆盖
                if (old_payload.get("candidate_type") or "") == "chat_followup":
                    if (c.context.get("candidate_type") or "") != "chat_followup":
                        continue
                old_score = float(old_payload.get("score") or 0)
                if c.score <= old_score:
                    self._skipped.append({"reason": "已有更高分待发私聊", "type": c.context.get("candidate_type", "")})
                    return None
                self.intents.cancel(user_id, persona_id, old["id"], reason="replaced_by_higher")

        payload = {
            **c.context,
            "score": c.score,
            "topic": c.topic,
            "context_points": c.context.get("context_points") or [c.reason],
            "observe_only": False,
        }
        status = "ready"
        return self.intents.create(
            user_id=user_id,
            persona_id=persona_id,
            action_kind=c.kind,
            reason_summary=c.reason,
            source=c.source,
            payload=payload,
            not_before=c.not_before,
            expires_at=c.expires_at,
            status=status,
            idempotency_key=c.idempotency_key or "",
        )

    def _routine_windows(self, user_id: str, persona_id: str) -> List[Candidate]:
        out: List[Candidate] = []
        conv = _persona_conv(user_id, persona_id)
        if not conv:
            return out
        first, last_end = _day_plan_bounds(user_id, persona_id)
        local = _local_now()
        today = date.today().isoformat()

        # 起床窗口
        wake_center = first
        if wake_center is None:
            wake_center = local.replace(hour=7, minute=30, second=0, microsecond=0).astimezone(timezone.utc)
        wake_h = wake_center.astimezone().hour
        score = 55.0
        if _hour_active_recently(user_id, conv, wake_h):
            score += 20
        nb, exp = _window_random(wake_center, before_min=30, after_min=40, ttl_hours=2.5)
        out.append(
            Candidate(
                kind="message",
                source="world",
                reason="作息：起床窗口想打个招呼",
                score=score,
                context={
                    "candidate_type": "routine_wake",
                    "context_points": ["起床窗口", f"约{wake_h}点"],
                },
                not_before=nb,
                expires_at=exp,
                idempotency_key=f"routine-wake:{user_id}:{persona_id}:{today}",
            )
        )

        # 睡前窗口：当天聊过才触发
        sleep_center = last_end
        if sleep_center is None:
            sleep_center = local.replace(hour=23, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        sleep_h = sleep_center.astimezone().hour
        score_s = 50.0
        if _user_chatted_today(user_id, conv):
            score_s += 25
        else:
            score_s -= 30
        if _hour_active_recently(user_id, conv, sleep_h):
            score_s += 10
        nb2, exp2 = _window_random(sleep_center, before_min=20, after_min=50, ttl_hours=3.0)
        out.append(
            Candidate(
                kind="message",
                source="world",
                reason="作息：睡前想关心你一下",
                score=score_s,
                context={
                    "candidate_type": "routine_sleep",
                    "context_points": ["睡前窗口", f"约{sleep_h}点"],
                },
                not_before=nb2,
                expires_at=exp2,
                idempotency_key=f"routine-sleep:{user_id}:{persona_id}:{today}",
            )
        )
        return out

    def _world_experiences(self, user_id: str, persona_id: str) -> List[Candidate]:
        if not worldline_enabled(user_id, persona_id):
            return []
        out: List[Candidate] = []
        events = _recent_world_events(user_id, persona_id, hours=4.0)
        st = get_world_state(user_id, persona_id) or {}
        activity = (st.get("current_activity") or "").strip()
        today = date.today().isoformat()
        for ev in events[:4]:
            kind = ev["kind"]
            payload = ev["payload"] or {}
            title = payload.get("title") or payload.get("summary") or activity or "刚忙完一事"
            rule = payload.get("rule") or ""
            if kind == "activity_complete":
                # 私聊或朋友圈二选一倾向：完成活动偏朋友圈，扰动偏私聊
                nb, exp = _window_random(_now(), before_min=0, after_min=40, ttl_hours=4)
                out.append(
                    Candidate(
                        kind="moment",
                        source="world",
                        reason=f"想发朋友圈聊聊：{title}",
                        score=58.0,
                        context={
                            "candidate_type": "world_share",
                            "activity": title,
                            "event_id": ev["id"],
                            "context_points": [f"刚完成：{title}"],
                        },
                        not_before=nb,
                        expires_at=exp,
                        idempotency_key=f"moment-act:{user_id}:{persona_id}:{ev['id']}",
                    )
                )
                out.append(
                    Candidate(
                        kind="message",
                        source="world",
                        reason=f"刚做完「{title}」，想跟你说两句",
                        score=52.0,
                        context={
                            "candidate_type": "world_share",
                            "activity": title,
                            "event_id": ev["id"],
                            "context_points": [f"活动：{title}"],
                        },
                        not_before=nb,
                        expires_at=exp,
                        idempotency_key=f"msg-act:{user_id}:{persona_id}:{ev['id']}",
                    )
                )
            elif kind == "disturbance":
                nb, exp = _window_random(_now(), before_min=0, after_min=25, ttl_hours=3)
                out.append(
                    Candidate(
                        kind="message",
                        source="world",
                        reason=payload.get("summary") or "刚才心里晃了一下，想找你聊聊",
                        score=60.0 if rule == "old_photo" else 48.0,
                        context={
                            "candidate_type": "world_disturb",
                            "rule": rule,
                            "event_id": ev["id"],
                            "context_points": [payload.get("summary") or rule],
                        },
                        not_before=nb,
                        expires_at=exp,
                        idempotency_key=f"msg-dist:{user_id}:{persona_id}:{ev['id']}",
                    )
                )
            elif kind == "weather_change":
                to_w = payload.get("to") or ""
                nb, exp = _window_random(_now(), before_min=0, after_min=50, ttl_hours=4)
                out.append(
                    Candidate(
                        kind="message",
                        source="world",
                        reason=f"天气变成{to_w}了，随口跟你念叨一声",
                        score=45.0,
                        context={
                            "candidate_type": "world_weather",
                            "weather": to_w,
                            "event_id": ev["id"],
                            "context_points": [f"天气：{to_w}"],
                        },
                        not_before=nb,
                        expires_at=exp,
                        idempotency_key=f"msg-wx:{user_id}:{persona_id}:{today}:{to_w}",
                    )
                )
        return out

    def _mood_reachout(self, user_id: str, persona_id: str) -> List[Candidate]:
        affect = AffectService().get_or_init(user_id, persona_id)
        worry = int(affect.get("worry") or 0)
        sadness = int(affect.get("sadness") or 0)
        if worry < WORRY_THRESHOLD and sadness < SADNESS_THRESHOLD:
            return []
        today = date.today().isoformat()
        dim = "担心" if worry >= sadness else "有点低落"
        nb, exp = _window_random(_now(), before_min=0, after_min=60, ttl_hours=5)
        return [
            Candidate(
                kind="message",
                source="world",
                reason=f"心里{dim}，想找你说说话或惦记你上次提的事",
                score=50.0 + max(worry, sadness) * 0.25,
                context={
                    "candidate_type": "mood_reachout",
                    "context_points": [f"心情：{dim}", f"worry={worry}", f"sadness={sadness}"],
                },
                not_before=nb,
                expires_at=exp,
                idempotency_key=f"mood-reach:{user_id}:{persona_id}:{today}",
            )
        ]

    def _lonely(self, user_id: str, persona_id: str) -> List[Candidate]:
        conv = _persona_conv(user_id, persona_id)
        if not conv:
            return []
        last = _last_user_msg_at(user_id, conv)
        if last is None:
            hours = 999.0
        else:
            hours = (_now() - last).total_seconds() / 3600.0
        if hours < LONELY_HOURS:
            return []
        today = date.today().isoformat()
        nb, exp = _window_random(_now(), before_min=0, after_min=90, ttl_hours=6)
        return [
            Candidate(
                kind="message",
                source="world",
                reason=f"有一会没聊了（约{int(hours)}小时），想轻轻问一声",
                score=38.0 + min(20.0, (hours - LONELY_HOURS) * 2),
                context={
                    "candidate_type": "lonely",
                    "hours_silent": round(hours, 1),
                    "context_points": [f"已{int(hours)}小时没说话"],
                },
                not_before=nb,
                expires_at=exp,
                idempotency_key=f"lonely:{user_id}:{persona_id}:{today}",
            )
        ]

    def _store_plan_log(
        self,
        user_id: str,
        persona_id: str,
        trigger: str,
        accepted: List[Dict[str, Any]],
    ) -> None:
        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS proactive_plan_logs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id TEXT NOT NULL,
                  persona_id TEXT NOT NULL,
                  trigger TEXT,
                  accepted_json TEXT,
                  skipped_json TEXT,
                  budget_json TEXT,
                  created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                INSERT INTO proactive_plan_logs(
                  user_id, persona_id, trigger, accepted_json, skipped_json, budget_json, created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    persona_id,
                    trigger,
                    json.dumps(
                        [{"id": a.get("id"), "kind": a.get("action_kind"), "reason": a.get("reason_summary")} for a in accepted],
                        ensure_ascii=False,
                    ),
                    json.dumps(self._skipped[:20], ensure_ascii=False),
                    json.dumps(self.gates.budget_status(user_id, persona_id), ensure_ascii=False),
                    iso(),
                ),
            )
        except Exception:
            pass


def build_message_prompt(intent: Dict[str, Any], *, affect: Dict[str, Any], activity: str) -> str:
    """执行时拼内部指令。"""
    try:
        payload = json.loads(intent.get("payload_json") or "{}")
    except Exception:
        payload = {}
    reason = (intent.get("reason_summary") or "").strip()
    points = payload.get("context_points") or []
    ctype = payload.get("candidate_type") or ""
    mood_bits = []
    for d in ("joy", "worry", "sadness", "annoyance", "curiosity"):
        v = int(affect.get(d) or 0)
        if abs(v - 35) >= 12:
            mood_bits.append(f"{d}={v}")
    lines = [
        "【系统内部指令·主动私聊】你现在主动找主人说话，不是回复某一句。",
        f"触发理由：{reason}",
        f"候选类型：{ctype or 'general'}",
    ]
    if points:
        lines.append("上下文要点：" + "；".join(str(p) for p in points[:4]))
    if activity:
        lines.append(f"你当前世界线活动：{activity}")
    if mood_bits:
        lines.append("你当前心情：" + ", ".join(mood_bits))
    lines.append(
        "要求：一两句口语，像突然想起来才发；带一点情绪；不要说这是自动提醒/系统消息/AI；"
        "不要编造主人没说过的具体日期；可配 ⟦表情：…⟧ 或 ⟦图：…⟧。"
    )
    return "\n".join(lines)


def build_moment_prompt(intent: Dict[str, Any], *, affect: Dict[str, Any], activity: str) -> str:
    try:
        payload = json.loads(intent.get("payload_json") or "{}")
    except Exception:
        payload = {}
    act = payload.get("activity") or activity or "刚忙完一件事"
    mood = []
    if int(affect.get("joy") or 0) >= 50:
        mood.append("偏开心")
    if int(affect.get("sadness") or 0) >= 50:
        mood.append("有点感慨")
    if int(affect.get("curiosity") or 0) >= 50:
        mood.append("有点好奇")
    return (
        f"你正在发一条朋友圈。此刻经历：{act}。心情：{'、'.join(mood) or '平常'}。\n"
        "只输出朋友圈正文本身：一两句口语，像随手发的。\n"
        "禁止：标题、标签、emoji、引号包裹、解释、询问是否合适、"
        "「写完啦」「正文如下」「你看合适不」「换一版」等元话语；禁止提及私聊内容。"
    )


_META_MOMENT_RE = re.compile(
    r"(写完|正文|如下|你看合适|要不要|换一版|草稿|文案|朋友圈内容|"
    r"输出如下|仅正文|以下是|我帮你写|可以发)"
)


def sanitize_moment_text(raw: str, *, fallback: str = "") -> str:
    """去掉模型串戏的元话语，只留可上屏的朋友圈正文。"""
    text = (raw or "").strip()
    if not text:
        return (fallback or "").strip()
    # 去掉常见包裹
    text = text.strip("「」『』“”\"'`")
    # 若含「正文就这一句：」之类，尽量截取冒号后内容
    for sep in ("正文就这一句：", "正文如下：", "正文：", "可以发：", "：\n", ":\n"):
        if sep in text:
            text = text.split(sep, 1)[-1].strip()
            break
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    keep: List[str] = []
    for ln in lines:
        if _META_MOMENT_RE.search(ln):
            continue
        if ln.startswith("（") and ln.endswith("）"):
            continue
        keep.append(ln.strip("「」『』“”\"'"))
    text = "".join(keep) if len(keep) <= 1 else "\n".join(keep[:2])
    text = text.strip()
    # 仍像元话语则回退
    if not text or _META_MOMENT_RE.search(text) or len(text) < 4:
        return (fallback or "").strip()
    # 过长截断到约两句
    if len(text) > 80:
        for punct in ("。", "！", "？", "~", "…"):
            idx = text.find(punct, 12)
            if 12 <= idx <= 80:
                text = text[: idx + 1]
                break
        else:
            text = text[:80].rstrip()
    return text


def latest_plan_summary(user_id: str, persona_id: str) -> Dict[str, Any]:
    try:
        row = db.fetchone(
            """
            SELECT trigger, accepted_json, skipped_json, budget_json, created_at
            FROM proactive_plan_logs
            WHERE user_id=? AND persona_id=?
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, persona_id),
        )
    except Exception:
        return {}
    if not row:
        return {}

    def _j(s: str) -> Any:
        try:
            return json.loads(s or "null")
        except Exception:
            return None

    return {
        "trigger": row["trigger"],
        "accepted": _j(row["accepted_json"]),
        "skipped": _j(row["skipped_json"]),
        "budget": _j(row["budget_json"]),
        "created_at": row["created_at"],
    }


def upcoming_intents_for_persona(user_id: str, persona_id: str, limit: int = 5) -> List[str]:
    rows = IntentService().list_open(user_id, persona_id, limit=limit)
    out: List[str] = []
    for r in rows:
        kind = r.get("action_kind") or "message"
        label = "主动私聊" if kind == "message" else ("朋友圈" if kind == "moment" else kind)
        reason = (r.get("reason_summary") or "")[:40]
        nb = r.get("not_before") or ""
        t = _parse_ts(nb)
        when = t.astimezone().strftime("%m-%d %H:%M") if t else "待定"
        out.append(f"{label}（约 {when}）{('：' + reason) if reason else ''}")
    return out
