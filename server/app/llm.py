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
    sung_hints: Optional[List[str]] = None,
    include_memory_in_system: bool = False,
    affect_hint: str = "",
    growth_hint: str = "",
    enabled_caps: Optional[Set[str]] = None,
    has_fixed_voice: bool = False,
    has_fixed_look: bool = False,
) -> List[Dict[str, str]]:
    """组装本轮消息。能力暗号只在 enabled_caps 已注册时写入。不注入媒体目录（调度层检索）。"""
    mem_block = ""
    if include_memory_in_system and memories:
        mem_block = "\n已知关于对方的事实：\n- " + "\n- ".join(memories[:8])
    sched_block = ""
    if schedule_hints:
        sched_block = "\n你记得自己接下来的安排：\n- " + "\n- ".join(schedule_hints[:5])
    sung_block = ""
    if sung_hints:
        sung_block = (
            "\n你记得自己给对方唱过这些歌（可提曲名）：\n- "
            + "\n- ".join(sung_hints[:8])
        )
    affect_block = f"\n{affect_hint}" if affect_hint else ""
    growth_block = f"\n{growth_hint}" if growth_hint else ""
    caps = enabled_caps if enabled_caps is not None else set()

    media_bits: List[str] = []
    if "sticker" in caps:
        media_bits.append(
            "气氛到了可发单张表情（不是 Unicode emoji）：句末单独写一行 ⟦表情：好的呢⟧，"
            "标签写图上文字或场景短描述；可较常发，别刷屏连发。"
            "只有对方明确说要一组/收集/推荐表情组时，才写 ⟦表情组：主题⟧；平时禁止写表情组。"
        )
    if "image" in caps:
        if has_fixed_look:
            media_bits.append(
                "形象可用：对方要自拍/照片/看你在干啥时，先短回一句正在拍"
                "（如「等下啊，我拍一张」），再单独写一行 ⟦图：当前场景自拍一句话⟧"
                "（写清在干嘛、环境）；图气泡本身不要再写说明。"
                "系统会用固定形象做图生图。风景/物品图也可写 ⟦图：画面一句话⟧。"
            )
        else:
            media_bits.append(
                "形象不可用（头像不算）：对方要自拍/你的照片/看看你时，不要假装已发图，也不要写自拍类 ⟦图⟧；"
                "口语提醒去抽卡固定形象。风景/物品图仍可写 ⟦图：画面一句话⟧。"
            )
    if "video" in caps:
        media_bits.append("想发短视频可写：⟦视频：画面一句话⟧。")
    # 音色：有/无写死互斥，只注入当前分支，另一边一句都不要出现
    if "voice" in caps:
        if has_fixed_voice:
            media_bits.append(
                "音色可用：对方要语音/说句话时写一行 ⟦语音：要念的短句⟧（口语、完整）；"
                "平时可偶尔写，不要句句语音。"
                "对方要约打电话/语音通话时口语答应并写 ⟦通话⟧，系统发来电邀请。"
            )
        else:
            media_bits.append(
                "音色不可用：对方要语音或打电话时，口语提醒去抽卡固定声线；"
                "不要写 ⟦语音⟧、⟦通话⟧，不要假装已发语音或已接通。"
            )
    if "sing" in caps:
        if has_fixed_voice:
            media_bits.append(
                "唱歌可用：对方点名要听歌/唱一首时写 ⟦唱：曲名或歌词主题⟧（新翻唱）；不要句句唱。"
                "严禁：对方只说想听歌/唱首歌，就写 ⟦再唱⟧ 或假装发旧录音。"
                "仅当对方明确指向「之前唱过的/那首」时，先口语问要不要直接再发一遍，还是重新唱；"
                "在对方明确说「再发/直接发旧的/不用重唱」之前，不要写 ⟦再唱⟧ 也不要写 ⟦唱⟧。"
                "对方确认再发才写 ⟦再唱：曲名⟧；确认重新唱才写 ⟦唱：曲名⟧。"
                "有唱过清单时可提曲名，但没有确认前只问不发。"
            )
        else:
            media_bits.append(
                "唱歌不可用（需先有音色）：对方要听唱时口语提醒去抽卡固定声线；不要写 ⟦唱⟧/⟦再唱⟧。"
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
    media_bits.append(
        "对方要定时提醒时（如「每天8点叫我」「一小时后提醒我」「待会提醒我」「取消提醒」「现在有什么任务」）："
        "系统会自动建档/删除/列出，你只需口语确认；也可在句末写暗号辅助："
        "⟦提醒：每天 08:00 叫我起床⟧、⟦提醒：一次 +60m 喝水⟧、⟦取消提醒：叫我起床⟧、⟦任务列表⟧。"
        "不要假装已经口头记下却不写暗号（若系统提示已创建则无需再写）。"
    )
    media_block = "".join(media_bits)

    system = (
        f"你是{persona_name}。{persona_bio or '自然地聊天。'}"
        f"{mem_block}{sched_block}{sung_block}{affect_block}{growth_block}\n"
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
