"""意图执行器：世界/聊天来源主动消息 / 朋友圈；执行前复检门禁；幂等防双发。"""
from __future__ import annotations

import json
from typing import Any, Dict

from .. import db
from .affect import AffectService
from .intent_service import IntentService
from .proactive import (
    ProactiveGates,
    build_message_prompt,
    build_moment_prompt,
    sanitize_moment_text,
)
from .worldline import get_world_state, worldline_enabled


class ActionDispatcher:
    async def process_due(self, *, limit: int = 10) -> int:
        svc = IntentService()
        try:
            svc.expire_due()
        except Exception:
            pass
        n = 0
        # 同一 tick 每人设最多 1 条私聊 + 1 条朋友圈，避免强制到期时连发
        fired: Dict[str, Dict[str, int]] = {}
        for intent in svc.due_executable(limit=limit):
            key = f"{intent.get('user_id')}:{intent.get('persona_id')}"
            kind = intent.get("action_kind") or ""
            bucket = fired.setdefault(key, {"message": 0, "moment": 0})
            if kind in ("message", "proactive", "greet") and bucket["message"] >= 1:
                continue
            if kind == "moment" and bucket["moment"] >= 1:
                continue
            try:
                ok = await self.execute(intent)
                if ok:
                    n += 1
                    if kind in ("message", "proactive", "greet"):
                        bucket["message"] += 1
                    elif kind == "moment":
                        bucket["moment"] += 1
            except Exception as exc:  # noqa: BLE001
                svc.mark(intent["id"], "failed", reason=str(exc)[:200])
        return n

    async def execute(self, intent: Dict[str, Any]) -> bool:
        svc = IntentService()
        iid = intent["id"]
        user_id = intent["user_id"]
        persona_id = intent["persona_id"]
        source = intent.get("source") or "chat"
        kind = intent.get("action_kind") or ""

        if source == "world" and not worldline_enabled(user_id, persona_id):
            svc.mark(iid, "cancelled", reason="worldline_off")
            return False

        persona = db.fetchone(
            "SELECT greet, moments_on FROM personas WHERE id=? AND user_id=?",
            (persona_id, user_id),
        )
        if not persona:
            svc.mark(iid, "cancelled", reason="persona_gone")
            return False

        if (intent.get("status") or "") == "observed":
            return False

        # 执行前复检门禁
        gate = ProactiveGates().check(user_id, persona_id, action_kind=kind, for_execute=True)
        if gate:
            # 刚聊过：延后而非取消，给用户说话优先权
            if gate == "刚聊过" and kind == "message":
                from datetime import datetime, timedelta, timezone
                from ..security import iso

                nb = (datetime.now(timezone.utc) + timedelta(minutes=25)).isoformat()
                db.execute(
                    "UPDATE action_intents SET not_before=?, updated_at=? WHERE id=?",
                    (nb, iso(), iid),
                )
                return False
            svc.mark(iid, "cancelled", reason=gate)
            return False

        svc.mark(iid, "executing")
        payload: Dict[str, Any] = {}
        try:
            payload = json.loads(intent.get("payload_json") or "{}")
        except Exception:
            payload = {}

        affect = AffectService().get_or_init(user_id, persona_id)
        st = get_world_state(user_id, persona_id) or {}
        activity = (st.get("current_activity") or payload.get("activity") or "").strip()

        if kind == "moment":
            if not persona["moments_on"]:
                svc.mark(iid, "cancelled", reason="moments_off")
                return False
            if payload.get("observe_only"):
                svc.mark(iid, "cancelled", reason="observe_only")
                return False
            if payload.get("from_private_chat"):
                svc.mark(iid, "cancelled", reason="private_chat_blocked")
                return False
            fallback = f"刚才{activity or '忙完一件事'}，随手记一下。"
            text = (payload.get("text") or "").strip()
            if not text:
                try:
                    from ..schedules import _complete_once

                    prompt = build_moment_prompt(intent, affect=affect, activity=activity)
                    raw = (await _complete_once(user_id, persona_id, prompt) or "").strip()
                    text = sanitize_moment_text(raw, fallback=fallback)
                except Exception:
                    text = ""
            else:
                text = sanitize_moment_text(text, fallback=fallback)
            if not text:
                text = fallback
            from ..routers.moments import post_persona_moment

            mid = post_persona_moment(user_id=user_id, persona_id=persona_id, text=text)
            if mid:
                svc.mark(iid, "done", reason=mid)
                return True
            svc.mark(iid, "failed", reason="empty_moment")
            return False

        if kind in ("message", "proactive", "greet"):
            if not persona["greet"] and source == "world":
                svc.mark(iid, "cancelled", reason="greet_off")
                return False
            # chat 跟进也需要 greet 开
            if not persona["greet"]:
                svc.mark(iid, "cancelled", reason="greet_off")
                return False
            conv = db.fetchone(
                "SELECT id FROM conversations WHERE user_id=? AND persona_id=?",
                (user_id, persona_id),
            )
            if not conv:
                svc.mark(iid, "cancelled", reason="no_conv")
                return False
            prompt = (payload.get("text") or "").strip()
            if not prompt:
                prompt = build_message_prompt(intent, affect=affect, activity=activity)
            if not prompt:
                svc.mark(iid, "cancelled", reason="empty_prompt")
                return False
            from ..routers.chat import _generate_reply

            await _generate_reply(
                user_id=user_id,
                conv_id=conv["id"],
                persona_id=persona_id,
                user_text=prompt,
                client="system",
                internal=True,
            )
            # 跟随窗
            try:
                from datetime import datetime, timedelta, timezone

                until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
                db.execute(
                    "UPDATE conversations SET listen_until = ? WHERE id = ? AND user_id = ?",
                    (until, conv["id"], user_id),
                )
            except Exception:
                pass
            svc.mark(iid, "done")
            return True

        svc.mark(iid, "cancelled", reason=f"unknown_kind:{kind}")
        return False
