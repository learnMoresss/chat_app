"""世界线运行层：可开关；观察模式先落库不对外发。"""
from __future__ import annotations

import json
import os
import random
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .. import db
from ..security import iso, new_id
from .events import emit_event
from .intent_service import IntentService
from .weather import resolve_weather

# 观察模式：只写 observed 意图，不 dispatch（阶段3）；阶段4 设 WORLDLINE_ACTIONS=1
ACTIONS_ENABLED = (os.environ.get("WORLDLINE_ACTIONS") or "1").strip() in ("1", "true", "yes")

_DISTURB_RULES = (
    ("old_photo", "翻到一张旧照片，有点感慨", 0.08),
    ("msg_noise", "收到一条无关群消息，晃了一下神", 0.05),
    ("stretch", "起身活动了一下脖子", 0.1),
)


def worldline_enabled(user_id: str, persona_id: str) -> bool:
    row = db.fetchone(
        "SELECT worldline_enabled FROM personas WHERE id=? AND user_id=?",
        (persona_id, user_id),
    )
    if not row:
        return False
    try:
        return bool(int(row["worldline_enabled"]))
    except Exception:
        return True


def set_worldline_enabled(user_id: str, persona_id: str, enabled: bool) -> None:
    was = worldline_enabled(user_id, persona_id)
    db.execute(
        "UPDATE personas SET worldline_enabled=? WHERE id=? AND user_id=?",
        (1 if enabled else 0, persona_id, user_id),
    )
    if was and not enabled:
        IntentService().cancel_world_intents(user_id, persona_id, reason="worldline_off")
        emit_event(
            user_id=user_id,
            persona_id=persona_id,
            kind="worldline_off",
            source="system",
            payload={},
        )
    elif (not was) and enabled:
        emit_event(
            user_id=user_id,
            persona_id=persona_id,
            kind="worldline_on",
            source="system",
            payload={},
        )
        # 重置 wake，触发重新对齐
        now = iso()
        db.execute(
            """
            INSERT INTO world_state(
              user_id, persona_id, location, weather, weather_source,
              current_activity_id, current_activity, interrupt_policy,
              next_wake_at, day_plan_date, state_json, revision, updated_at
            ) VALUES (?,?, '', '', 'sim', '', '', 'busy_soft', ?, '', '{}', 1, ?)
            ON CONFLICT(user_id, persona_id) DO UPDATE SET
              next_wake_at=excluded.next_wake_at, updated_at=excluded.updated_at,
              revision=world_state.revision+1
            """,
            (user_id, persona_id, now, now),
        )


def get_world_state(user_id: str, persona_id: str) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT * FROM world_state WHERE user_id=? AND persona_id=?",
        (user_id, persona_id),
    )
    return dict(row) if row else None


def _next_planned_activity(user_id: str, persona_id: str) -> Optional[Dict[str, Any]]:
    now_s = datetime.now(timezone.utc).isoformat()
    return db.fetchone(
        """
        SELECT id, title, starts_at, ends_at FROM world_activities
        WHERE user_id=? AND persona_id=? AND status='planned'
          AND starts_at!='' AND starts_at>?
        ORDER BY starts_at ASC LIMIT 1
        """,
        (user_id, persona_id, now_s),
    )


def _local_hhmm(iso_s: str) -> str:
    try:
        t = datetime.fromisoformat(str(iso_s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone().strftime("%H:%M")
    except Exception:
        return ""


def presence_hint(user_id: str, persona_id: str) -> str:
    """处境注入：忙碌给表达指引；空闲注入空闲+下一项。立即回复，不拖延。"""
    if not worldline_enabled(user_id, persona_id):
        return ""
    st = get_world_state(user_id, persona_id)
    if not st:
        return ""
    act = (st.get("current_activity") or "").strip()
    weather = (st.get("weather") or "").strip()
    policy = (st.get("interrupt_policy") or "busy_soft").strip()
    parts: List[str] = []
    if weather:
        parts.append(f"天气：{weather}")
    nxt = _next_planned_activity(user_id, persona_id)
    nxt_label = ""
    if nxt:
        hh = _local_hhmm(nxt["starts_at"] or "")
        title = (nxt["title"] or "").strip() or "下一项安排"
        nxt_label = f"{hh} {title}".strip() if hh else title
    if act:
        parts.append(f"当前活动：{act}")
        if policy.startswith("busy") and "空闲" not in act and "休息" not in act and "自由" not in act:
            parts.append(
                "表达指引：你可能正忙于上述活动，语气可略忙；"
                "必须立即正常回复，不要声称自己延迟了、不要说「等忙完再说」。"
                "是否口头解释正在忙，按人设自行决定。"
            )
        elif nxt_label:
            parts.append(f"下一项：{nxt_label}")
    else:
        if nxt_label:
            parts.append(f"当前空闲，下一项：{nxt_label}")
        else:
            parts.append("当前空闲")
    return "；".join(parts)


class WorldlineService:
    def list_enabled_personas(self) -> List[Dict[str, str]]:
        rows = db.fetchall(
            """
            SELECT user_id, id AS persona_id FROM personas
            WHERE worldline_enabled=1 AND is_bot=1
            """
        )
        return [{"user_id": r["user_id"], "persona_id": r["persona_id"]} for r in rows]

    async def tick_all(self) -> int:
        n = 0
        for item in self.list_enabled_personas():
            try:
                if await self.tick(item["user_id"], item["persona_id"]):
                    n += 1
            except Exception:
                continue
        return n

    async def tick(self, user_id: str, persona_id: str) -> bool:
        if not worldline_enabled(user_id, persona_id):
            return False
        now = datetime.now(timezone.utc)
        st = get_world_state(user_id, persona_id)
        if st is None:
            await self._ensure_profile_and_state(user_id, persona_id)
            st = get_world_state(user_id, persona_id)
            if st is None:
                return False
        wake = (st.get("next_wake_at") or "").strip()
        if wake:
            try:
                t0 = datetime.fromisoformat(wake.replace("Z", "+00:00"))
                if t0.tzinfo is None:
                    t0 = t0.replace(tzinfo=timezone.utc)
                if t0 > now:
                    # 仍可推进确定性活动到点
                    self._advance_activities(user_id, persona_id, now)
                    return False
            except Exception:
                pass

        await self._ensure_day_plan(user_id, persona_id)
        w = await resolve_weather(
            user_id=user_id,
            persona_id=persona_id,
            location=(st.get("location") or "") or self._profile_location(user_id, persona_id),
        )
        self._apply_weather(user_id, persona_id, w)
        self._advance_activities(user_id, persona_id, now)
        self._maybe_disturb(user_id, persona_id)
        # 朋友圈/主动私聊改由 ProactivePlanner 机会驱动，不再用固定概率模板
        try:
            from .proactive import ProactivePlanner

            ProactivePlanner().plan(user_id, persona_id, trigger="world_tick")
        except Exception:
            pass
        # 下次唤醒：15～45 分钟
        nxt = now + timedelta(minutes=random.randint(15, 45))
        db.execute(
            "UPDATE world_state SET next_wake_at=?, updated_at=?, revision=revision+1 WHERE user_id=? AND persona_id=?",
            (nxt.isoformat(), iso(), user_id, persona_id),
        )
        return True

    def _profile_location(self, user_id: str, persona_id: str) -> str:
        row = db.fetchone(
            "SELECT profile_json FROM world_profiles WHERE user_id=? AND persona_id=?",
            (user_id, persona_id),
        )
        if not row:
            return ""
        try:
            return str((json.loads(row["profile_json"] or "{}")).get("location") or "")
        except Exception:
            return ""

    async def _ensure_profile_and_state(self, user_id: str, persona_id: str) -> None:
        exists = db.fetchone(
            "SELECT persona_id FROM world_profiles WHERE user_id=? AND persona_id=?",
            (user_id, persona_id),
        )
        persona = db.fetchone(
            "SELECT name, bio, remark FROM personas WHERE id=? AND user_id=?",
            (persona_id, user_id),
        )
        now = iso()
        if not exists:
            # 规则初始化（不强制调模型）；bio 里已有信息优先
            bio = (persona["bio"] if persona else "") or ""
            profile = {
                "location": "本地城市",
                "timezone": "local",
                "occupation": "",
                "routine": "日间活动、晚间休息",
                "interests": [],
                "from_bio_excerpt": bio[:400],
                "fiction": True,
            }
            db.execute(
                """
                INSERT INTO world_profiles(user_id, persona_id, profile_json, source_kind, revision, created_at, updated_at)
                VALUES (?,?,?,?,1,?,?)
                """,
                (user_id, persona_id, json.dumps(profile, ensure_ascii=False), "fiction", now, now),
            )
            emit_event(
                user_id=user_id,
                persona_id=persona_id,
                kind="world_init",
                source="world",
                payload={"profile": profile},
            )
        if get_world_state(user_id, persona_id) is None:
            db.execute(
                """
                INSERT INTO world_state(
                  user_id, persona_id, location, weather, weather_source,
                  current_activity_id, current_activity, interrupt_policy,
                  next_wake_at, day_plan_date, state_json, revision, updated_at
                ) VALUES (?,?, '本地城市', '', 'sim', '', '', 'busy_soft', ?, '', '{}', 1, ?)
                """,
                (user_id, persona_id, now, now),
            )

    async def _ensure_day_plan(self, user_id: str, persona_id: str) -> None:
        today = date.today().isoformat()
        st = get_world_state(user_id, persona_id) or {}
        if (st.get("day_plan_date") or "") == today:
            return
        plan_id = new_id("wp_")
        # 确定性日计划模板（观察模式不强制每日调 DSH；可选增强）
        blocks = [
            {"start": "08:00", "end": "09:00", "title": "起床洗漱"},
            {"start": "09:30", "end": "12:00", "title": "做自己的事"},
            {"start": "12:00", "end": "13:00", "title": "午饭休息"},
            {"start": "14:00", "end": "17:30", "title": "下午安排"},
            {"start": "19:00", "end": "21:00", "title": "晚饭与放松"},
            {"start": "22:30", "end": "23:30", "title": "准备休息"},
        ]
        blocks = self._fill_day_gaps(blocks)
        # 若开启且配额允许，可用 DSH 润色标题（失败则保留模板）
        if (os.environ.get("WORLDLINE_DSH_PLAN") or "").strip() in ("1", "true", "yes"):
            try:
                from .dsh_adapter import DshAdapter

                persona = db.fetchone(
                    "SELECT bio, name FROM personas WHERE id=? AND user_id=?",
                    (persona_id, user_id),
                )
                prompt = (
                    "根据人设生成今天的 JSON 日计划数组，每项含 start,end,title（本地时间 HH:MM）。"
                    "最多 6 段。只输出 JSON。\n人设："
                    + ((persona["bio"] if persona else "") or "")[:600]
                )
                raw = await DshAdapter().complete(
                    purpose="plan",
                    user_id=user_id,
                    persona_id=persona_id,
                    text=prompt,
                    system="只输出 JSON 数组",
                )
                cleaned = raw.strip()
                if "```" in cleaned:
                    cleaned = cleaned.split("```")[1]
                    if cleaned.startswith("json"):
                        cleaned = cleaned[4:]
                data = json.loads(cleaned)
                if isinstance(data, list) and data:
                    blocks = [
                        {
                            "start": str(x.get("start") or "09:00"),
                            "end": str(x.get("end") or "10:00"),
                            "title": str(x.get("title") or "活动")[:40],
                        }
                        for x in data[:6]
                    ]
                    blocks = self._fill_day_gaps(blocks)
            except Exception:
                pass

        now = iso()
        db.execute(
            """
            INSERT INTO world_plans(id, user_id, persona_id, plan_date, blocks_json, status, created_at, updated_at)
            VALUES (?,?,?,?,?, 'active', ?, ?)
            """,
            (plan_id, user_id, persona_id, today, json.dumps(blocks, ensure_ascii=False), now, now),
        )
        # 物化为 activities
        local = datetime.now().astimezone()
        for b in blocks:
            aid = new_id("wa_")
            starts = self._combine_local(local, b["start"])
            ends = self._combine_local(local, b["end"])
            db.execute(
                """
                INSERT INTO world_activities(
                  id, user_id, persona_id, plan_id, title, status, interrupt_policy,
                  starts_at, ends_at, meta_json, created_at, updated_at
                ) VALUES (?,?,?,?,?, 'planned', 'busy_soft', ?, ?, '{}', ?, ?)
                """,
                (aid, user_id, persona_id, plan_id, b["title"], starts, ends, now, now),
            )
        db.execute(
            """
            UPDATE world_state SET day_plan_date=?, updated_at=?, revision=revision+1
            WHERE user_id=? AND persona_id=?
            """,
            (today, now, user_id, persona_id),
        )
        emit_event(
            user_id=user_id,
            persona_id=persona_id,
            kind="day_plan",
            source="world",
            payload={"plan_id": plan_id, "date": today},
        )

    def _combine_local(self, local_now: datetime, hhmm: str) -> str:
        try:
            hh, mm = hhmm.split(":")
            dt = local_now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            return iso()

    @staticmethod
    def _hhmm_to_min(hhmm: str) -> int:
        try:
            hh, mm = hhmm.split(":")
            return int(hh) * 60 + int(mm)
        except Exception:
            return 0

    @staticmethod
    def _min_to_hhmm(m: int) -> str:
        m = max(0, min(24 * 60 - 1, m))
        return f"{m // 60:02d}:{m % 60:02d}"

    def _fill_day_gaps(self, blocks: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """用「自由时间 / 在家休息」填满计划块之间的空档。"""
        if not blocks:
            return [
                {"start": "08:00", "end": "12:00", "title": "在家休息"},
                {"start": "12:00", "end": "18:00", "title": "自由时间"},
                {"start": "18:00", "end": "23:00", "title": "在家休息"},
            ]
        ordered = sorted(blocks, key=lambda b: self._hhmm_to_min(str(b.get("start") or "00:00")))
        filled: List[Dict[str, str]] = []
        cursor = self._hhmm_to_min("07:30")
        day_end = self._hhmm_to_min("23:30")
        idle_titles = ("自由时间", "在家休息")
        for i, b in enumerate(ordered):
            start = self._hhmm_to_min(str(b.get("start") or "09:00"))
            end = self._hhmm_to_min(str(b.get("end") or "10:00"))
            if end <= start:
                end = start + 30
            if start - cursor >= 20:
                filled.append(
                    {
                        "start": self._min_to_hhmm(cursor),
                        "end": self._min_to_hhmm(start),
                        "title": idle_titles[i % 2],
                    }
                )
            filled.append(
                {
                    "start": self._min_to_hhmm(start),
                    "end": self._min_to_hhmm(end),
                    "title": str(b.get("title") or "活动")[:40],
                }
            )
            cursor = max(cursor, end)
        if day_end - cursor >= 20:
            filled.append(
                {
                    "start": self._min_to_hhmm(cursor),
                    "end": self._min_to_hhmm(day_end),
                    "title": "在家休息",
                }
            )
        return filled

    def _ensure_idle_activity(self, user_id: str, persona_id: str, now: datetime) -> None:
        """无进行中活动时写入空档处境，避免模型瞎编。"""
        st = get_world_state(user_id, persona_id) or {}
        if (st.get("current_activity") or "").strip():
            return
        running = db.fetchone(
            """
            SELECT id FROM world_activities
            WHERE user_id=? AND persona_id=? AND status='running' LIMIT 1
            """,
            (user_id, persona_id),
        )
        if running:
            return
        nxt = _next_planned_activity(user_id, persona_id)
        title = "自由时间"
        if nxt:
            hh = _local_hhmm(nxt["starts_at"] or "")
            title = f"空闲（下一项 {hh} {(nxt['title'] or '').strip()}）".strip()
        aid = new_id("wa_")
        ends = (now + timedelta(minutes=30)).isoformat()
        now_s = iso()
        db.execute(
            """
            INSERT INTO world_activities(
              id, user_id, persona_id, plan_id, title, status, interrupt_policy,
              starts_at, ends_at, meta_json, created_at, updated_at
            ) VALUES (?,?,?,?,?, 'running', 'free', ?, ?, '{"gap":true}', ?, ?)
            """,
            (aid, user_id, persona_id, "", title, now.isoformat(), ends, now_s, now_s),
        )
        db.execute(
            """
            UPDATE world_state
            SET current_activity_id=?, current_activity=?, interrupt_policy='free',
                updated_at=?, revision=revision+1
            WHERE user_id=? AND persona_id=?
            """,
            (aid, title, now_s, user_id, persona_id),
        )
        emit_event(
            user_id=user_id,
            persona_id=persona_id,
            kind="activity_start",
            source="world",
            payload={"activity_id": aid, "title": title, "gap": True},
        )

    def _apply_weather(self, user_id: str, persona_id: str, w: Dict[str, Any]) -> None:
        prev = get_world_state(user_id, persona_id) or {}
        old_label = (prev.get("weather") or "").strip()
        new_label = (w.get("label") or "").strip()
        db.execute(
            """
            UPDATE world_state
            SET weather=?, weather_source=?, updated_at=?, revision=revision+1
            WHERE user_id=? AND persona_id=?
            """,
            (new_label, w.get("source") or "sim", iso(), user_id, persona_id),
        )
        if new_label and new_label != old_label:
            eid = emit_event(
                user_id=user_id,
                persona_id=persona_id,
                kind="weather_change",
                source="world",
                payload={"from": old_label, "to": new_label, "outdoor_ok": w.get("outdoor_ok", True)},
            )
            try:
                from .affect_rules import propose_from_world

                propose_from_world(
                    user_id=user_id,
                    persona_id=persona_id,
                    event_id=eid,
                    kind="weather_change",
                    weather_label=new_label,
                )
            except Exception:
                pass
        if not w.get("outdoor_ok", True):
            # 取消/改写含「户外/散步」的 planned
            rows = db.fetchall(
                """
                SELECT id, title FROM world_activities
                WHERE user_id=? AND persona_id=? AND status='planned'
                """,
                (user_id, persona_id),
            )
            for r in rows:
                title = r["title"] or ""
                if any(k in title for k in ("户外", "散步", "跑步", "出门")):
                    db.execute(
                        """
                        UPDATE world_activities
                        SET title=?, status='cancelled', updated_at=?
                        WHERE id=?
                        """,
                        (f"{title}（因天气改室内）", iso(), r["id"]),
                    )

    def _advance_activities(self, user_id: str, persona_id: str, now: datetime) -> None:
        now_s = now.isoformat()
        # 到点开始
        due_start = db.fetchall(
            """
            SELECT * FROM world_activities
            WHERE user_id=? AND persona_id=? AND status='planned'
              AND starts_at!='' AND starts_at<=?
            ORDER BY starts_at ASC LIMIT 3
            """,
            (user_id, persona_id, now_s),
        )
        for r in due_start:
            db.execute(
                "UPDATE world_activities SET status='running', updated_at=? WHERE id=?",
                (iso(), r["id"]),
            )
            db.execute(
                """
                UPDATE world_state
                SET current_activity_id=?, current_activity=?, interrupt_policy=?,
                    updated_at=?, revision=revision+1
                WHERE user_id=? AND persona_id=?
                """,
                (
                    r["id"],
                    r["title"] or "",
                    r["interrupt_policy"] or "busy_soft",
                    iso(),
                    user_id,
                    persona_id,
                ),
            )
            emit_event(
                user_id=user_id,
                persona_id=persona_id,
                kind="activity_start",
                source="world",
                payload={"activity_id": r["id"], "title": r["title"]},
            )
        # 到点结束
        due_end = db.fetchall(
            """
            SELECT * FROM world_activities
            WHERE user_id=? AND persona_id=? AND status='running'
              AND ends_at!='' AND ends_at<=?
            """,
            (user_id, persona_id, now_s),
        )
        for r in due_end:
            db.execute(
                "UPDATE world_activities SET status='completed', updated_at=? WHERE id=?",
                (iso(), r["id"]),
            )
            st = get_world_state(user_id, persona_id) or {}
            if (st.get("current_activity_id") or "") == r["id"]:
                db.execute(
                    """
                    UPDATE world_state
                    SET current_activity_id='', current_activity='', updated_at=?, revision=revision+1
                    WHERE user_id=? AND persona_id=?
                    """,
                    (iso(), user_id, persona_id),
                )
            eid = emit_event(
                user_id=user_id,
                persona_id=persona_id,
                kind="activity_complete",
                source="world",
                payload={"activity_id": r["id"], "title": r["title"]},
            )
            try:
                from .affect_rules import propose_from_world

                propose_from_world(
                    user_id=user_id,
                    persona_id=persona_id,
                    event_id=eid,
                    kind="activity_complete",
                    title=r["title"] or "",
                )
            except Exception:
                pass
        # 空档：没有 running 活动时补一个空闲处境
        self._ensure_idle_activity(user_id, persona_id, now)

    def _maybe_disturb(self, user_id: str, persona_id: str) -> None:
        # 冷却：24h 内同类不重复
        for key, summary, prob in _DISTURB_RULES:
            if random.random() > prob:
                continue
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            hit = db.fetchone(
                """
                SELECT id FROM world_disturbances
                WHERE user_id=? AND persona_id=? AND rule_key=? AND created_at>=?
                """,
                (user_id, persona_id, key, since),
            )
            if hit:
                continue
            did = new_id("wd_")
            db.execute(
                """
                INSERT INTO world_disturbances(id, user_id, persona_id, rule_key, summary, created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (did, user_id, persona_id, key, summary, iso()),
            )
            eid = emit_event(
                user_id=user_id,
                persona_id=persona_id,
                kind="disturbance",
                source="world",
                payload={"rule": key, "summary": summary, "disturbance_id": did},
            )
            try:
                from .affect_rules import propose_from_world

                propose_from_world(
                    user_id=user_id,
                    persona_id=persona_id,
                    event_id=eid,
                    kind="disturbance",
                    rule_key=key,
                )
            except Exception:
                pass
            break  # 一次 tick 最多一条
