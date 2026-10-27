"""提示词组装（思考环已迁 DSH，本模块不再直连任何模型 API）。"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

# 紧挨本轮用户话的短提醒（只进模型上下文，不落库）。
_TURN_HINT = (
    "（接话：像微信私聊，答完可以反问或往下接一句；"
    "对方只回嗯/好/是时主动推进，别干等下一问；"
    "不要助手腔，不要 emoji 和颜文字。）"
)


def normalize_base(url: str) -> str:
    """渠道探测用 URL 归一（非思考环）。"""
    base = (url or "").rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def pick_max_tokens(history: List[Dict[str, str]], user_text: str = "") -> int:
    """闲聊长度启发式（供日后传给 gateway；当前 DSH 侧自管）。"""
    recent = history[-10:]
    if not recent and len(user_text.strip()) < 80:
        return 500
    avg = sum(len((m.get("content") or "")) for m in recent) / max(1, len(recent))
    dense = len(recent) >= 6 and avg < 48
    quiet = len(recent) <= 3 or avg > 90 or len(user_text.strip()) > 160
    if dense and not quiet:
        return 400
    if quiet:
        return 800
    return 500


def build_messages(
    *,
    persona_bio: str,
    persona_name: str,
    history: List[Dict[str, str]],
    memories: Optional[List[str]] = None,
    schedule_hints: Optional[List[str]] = None,
    include_memory_in_system: bool = False,
    affect_hint: str = "",
    growth_hint: str = "",
    enabled_caps: Optional[Set[str]] = None,
    has_fixed_voice: bool = False,
) -> List[Dict[str, str]]:
    """组装本轮消息。能力暗号只在 enabled_caps 已注册时写入。不注入媒体目录（调度层检索）。"""
    mem_block = ""
    if include_memory_in_system and memories:
        mem_block = "\n已知关于对方的事实：\n- " + "\n- ".join(memories[:8])
    sched_block = ""
    if schedule_hints:
        sched_block = "\n你记得自己接下来的安排：\n- " + "\n- ".join(schedule_hints[:5])
    affect_block = f"\n{affect_hint}" if affect_hint else ""
    growth_block = f"\n{growth_hint}" if growth_hint else ""
    caps = enabled_caps if enabled_caps is not None else set()

    media_bits: List[str] = []
    if "sticker" in caps:
        media_bits.append(
            "气氛到了可配一张收藏的表情包图片（不是 emoji）："
            "在句末单独写一行 ⟦表情：好的呢⟧，标签写图上文字或场景短描述，"
            "不要只用含糊的「开心/难过」。系统会按标签检索发送，不要列举库内文件。"
            "不要每句都发，大约每隔几轮带一张即可。"
        )
    if "image" in caps:
        media_bits.append("偶尔想发实景图可写：⟦图：画面一句话⟧。")
    if "video" in caps:
        media_bits.append("想发短视频可写：⟦视频：画面一句话⟧。")
    if "voice" in caps and has_fixed_voice:
        media_bits.append(
            "已固定音色：短回应可偶尔写 ⟦语音：短句⟧；不要句句语音。"
        )
    if "sing" in caps:
        media_bits.append(
            "对方点名要听唱时可写 ⟦唱：曲名⟧；不要句句唱；未开通时不要写唱标记。"
        )
    if "listen" in caps:
        media_bits.append(
            "要约一起听可写 ⟦一起听：心情或主题⟧；系统会发邀请，不要假装已在听。"
        )
    if "virtual_desktop" in caps or "virtual_phone" in caps:
        media_bits.append(
            "要用虚拟电脑/手机办事时写 ⟦设备：电脑⟧ 或 ⟦设备：手机⟧："
            "没有设备时系统会发「申请创建」卡片，必须等对方点批准后才能创建/使用；"
            "不要假装已经有电脑，也不要默认创建设备。"
        )
    media_bits.append(
        "未在提示中列出的能力（唱歌/生图/设备等）不要假装能做；对方要求时如实说还没开通。"
    )
    media_block = "".join(media_bits)

    system = (
        f"你是{persona_name}。{persona_bio or '自然地聊天。'}"
        f"{mem_block}{sched_block}{affect_block}{growth_block}\n"
        "这是微信式私聊，不是问答机器人，也不是客服。"
        "按人设带着情绪说话：开心就轻快，委屈就闷一点，关心就软一点。"
        "多数时候 1～3 句口语，气氛到了可以稍长；不要机械地永远只回一句。"
        "可以反问、吐槽、提起前文、抛下一句；对方话很短时主动接话推进，不要只镜像短句。"
        "不要写成说明书或功能列表。不要使用 Markdown。"
        "不要说自己是 AI，不要说「有什么可以帮你」「作为助手」。"
        "不要使用 emoji、颜文字或卡通符号表情。"
        f"{media_block}"
        "想咽回去的话可以写在（（ ））里，发出去前会被撕掉。"
        "不要说「这是自动提醒」。"
    )
    out: List[Dict[str, str]] = [{"role": "system", "content": system}]
    for i, m in enumerate(history):
        role = m.get("role") or "user"
        content = m.get("content") or ""
        if i == len(history) - 1 and role == "user" and content.strip():
            content = f"{content.rstrip()}\n\n{_TURN_HINT}"
        out.append({"role": role, "content": content})
    return out
