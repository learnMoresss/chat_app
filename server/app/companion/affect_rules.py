"""回合后轻量情绪评估（规则优先，不另烧模型）。"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from .affect import AffectService

# 世界线事件 -> 心情增量（幂等 event_id 由调用方保证）
WORLD_AFFECT_MAP: Dict[str, Tuple[Dict[str, float], str]] = {
    "old_photo": ({"sadness": 6.0, "curiosity": 3.0}, "翻到旧照片"),
    "msg_noise": ({"annoyance": 4.0}, "群消息打扰"),
    "stretch": ({"excitement": 2.0, "joy": 2.0}, "起身活动"),
    "activity_complete": ({"joy": 5.0, "excitement": 2.0}, "活动完成"),
    "weather_rain": ({"sadness": 3.0, "joy": -2.0}, "下雨天"),
    "weather_sunny": ({"joy": 3.0, "excitement": 2.0}, "晴天"),
    "weather_bad": ({"sadness": 2.0, "worry": 2.0}, "天气不好"),
}


def deltas_from_world_event(
    *,
    kind: str,
    rule_key: str = "",
    title: str = "",
    weather_label: str = "",
) -> Tuple[Dict[str, float], str]:
    """根据世界线事件类型返回 deltas + reason。"""
    if kind == "disturbance" and rule_key in WORLD_AFFECT_MAP:
        return WORLD_AFFECT_MAP[rule_key]
    if kind == "activity_complete":
        deltas, reason = WORLD_AFFECT_MAP["activity_complete"]
        out = dict(deltas)
        t = title or ""
        if any(k in t for k in ("画", "散步", "咖啡", "看展", "电影")):
            out["joy"] = out.get("joy", 0) + 2
        if any(k in t for k in ("加班", "开会", "赶工")):
            out["annoyance"] = out.get("annoyance", 0) + 3
            out["joy"] = out.get("joy", 0) - 2
        return out, reason
    if kind == "weather_change":
        label = (weather_label or "").lower()
        if any(k in label for k in ("雨", "rain", "雷", "storm")):
            return WORLD_AFFECT_MAP["weather_rain"]
        if any(k in label for k in ("晴", "sun", "clear")):
            return WORLD_AFFECT_MAP["weather_sunny"]
        if any(k in label for k in ("阴", "雾", "霾", "雪", "cold", "凉")):
            return WORLD_AFFECT_MAP["weather_bad"]
    return {}, ""


def propose_from_world(
    *,
    user_id: str,
    persona_id: str,
    event_id: str,
    kind: str,
    rule_key: str = "",
    title: str = "",
    weather_label: str = "",
) -> Dict[str, Any]:
    """世界线事件写心情；无变化或重复 event_id 时静默。"""
    svc = AffectService()
    cur = svc.get_or_init(user_id, persona_id)
    deltas, reason = deltas_from_world_event(
        kind=kind, rule_key=rule_key, title=title, weather_label=weather_label
    )
    if not deltas or not event_id:
        return {"changed": False, "affect": cur, "deltas": {}, "reason": ""}
    try:
        nxt = svc.propose(
            user_id=user_id,
            persona_id=persona_id,
            deltas=deltas,
            event_id=event_id,
            reason=f"world:{reason}" if reason else "world_event",
            important=False,
        )
        return {"changed": True, "affect": nxt, "deltas": deltas, "reason": reason}
    except Exception:
        return {"changed": False, "affect": cur, "deltas": deltas, "reason": reason}


def deltas_from_user_text(user_text: str) -> Tuple[Dict[str, float], str]:
    t = (user_text or "").strip()
    if not t:
        return {}, ""
    deltas: Dict[str, float] = {}
    reasons: list[str] = []

    def bump(dim: str, v: float, why: str) -> None:
        deltas[dim] = deltas.get(dim, 0.0) + v
        if why not in reasons:
            reasons.append(why)

    if any(k in t for k in ("开心", "太好了", "哈哈", "嘿嘿", "谢谢", "爱你", "生日快乐", "过生日")):
        bump("joy", 8, "正向情绪词")
    if any(k in t for k in ("紧张", "焦虑", "担心", "害怕", "怎么办")):
        bump("worry", 8, "担心/紧张")
        bump("excitement", -3, "担心/紧张")
    if any(k in t for k in ("烦", "生气", "无语", "讨厌", "滚")):
        bump("annoyance", 8, "烦躁")
        bump("joy", -4, "烦躁")
    if any(k in t for k in ("难过", "伤心", "哭", "郁闷", "失落")):
        bump("sadness", 8, "低落")
        bump("joy", -4, "低落")
    if any(k in t for k in ("好奇", "为什么", "咋回事", "什么意思", "讲讲")):
        bump("curiosity", 6, "好奇")
    if any(k in t for k in ("兴奋", "激动", "太棒了", "冲")):
        bump("excitement", 8, "兴奋")
        bump("joy", 4, "兴奋")
    if any(k in t for k in ("过敏", "疼", "生病", "不舒服", "医院")):
        bump("worry", 6, "身体不适")
        bump("sadness", 3, "身体不适")
    if ("?" in t or "？" in t) and "curiosity" not in deltas:
        bump("curiosity", 2, "提问")

    # 限幅到 propose 正常上限附近
    capped = {k: max(-10.0, min(10.0, v)) for k, v in deltas.items() if abs(v) >= 1.5}
    return capped, "；".join(reasons[:3])


def propose_after_reply(
    *,
    user_id: str,
    persona_id: str,
    user_text: str,
    event_id: str,
) -> Dict[str, Any]:
    """幂等 propose；无变化返回当前视图。"""
    svc = AffectService()
    cur = svc.get_or_init(user_id, persona_id)
    deltas, reason = deltas_from_user_text(user_text)
    if not deltas or not event_id:
        return {"changed": False, "affect": cur, "deltas": {}, "reason": ""}
    try:
        nxt = svc.propose(
            user_id=user_id,
            persona_id=persona_id,
            deltas=deltas,
            event_id=event_id,
            reason=reason or "turn_rule",
            important=any(k in (user_text or "") for k in ("生日", "过敏", "分手", "去世")),
        )
        return {"changed": True, "affect": nxt, "deltas": deltas, "reason": reason}
    except Exception:
        return {"changed": False, "affect": cur, "deltas": deltas, "reason": reason}
