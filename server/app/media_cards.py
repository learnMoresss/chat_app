"""角色发图：真生图（Z-Image / Qwen-Image 2.1）优先，失败再画氛围卡。"""
from __future__ import annotations

import re
import textwrap
from typing import Optional, Tuple

from . import db
from .config import get_settings


def save_image_bytes(
    *,
    user_id: str,
    data: bytes,
    source: str = "persona",
    suffix: str = "png",
    content_type: str = "image/png",
) -> Optional[str]:
    if not data:
        return None
    from .media_store import put_bytes

    ext = suffix.lstrip(".") or "png"
    fid, _ = put_bytes(
        user_id=user_id,
        data=data,
        content_type=content_type,
        source=source,
        filename=f"{ext}.{ext}",
    )
    return fid


def make_mood_card(*, user_id: str, caption: str, persona_name: str = "") -> Optional[str]:
    """画一张 JPEG 氛围卡（生图不可用时的降级）。"""
    caption = (caption or "").strip()[:120]
    if not caption:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None

    w, h = 720, 960
    # 按文案偏移色相，避免不同 caption 在缺字字体下像素撞车被 stickers 去重
    seed = sum(ord(c) for c in caption) % 90
    c0 = (42 + seed // 3, 48 + (seed % 40), 62 + (seed % 50))
    c1 = (88 + seed // 4, 72 + (seed % 30), 98 + (seed % 40))
    img = Image.new("RGB", (w, h), c0)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(c0[0] + (c1[0] - c0[0]) * t)
        g = int(c0[1] + (c1[1] - c0[1]) * t)
        b = int(c0[2] + (c1[2] - c0[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    draw.rectangle([(24, 24), (24 + 12 + seed, 36)], fill=(200, 160 + seed % 40, 120))

    try:
        font_title = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 36)
        font_body = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 28)
    except Exception:
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
            font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
        except Exception:
            font_title = ImageFont.load_default()
            font_body = font_title

    title = (persona_name or "随手一拍").strip()[:16]
    draw.text((48, 72), title, fill=(230, 234, 242), font=font_title)
    lines = textwrap.wrap(caption, width=16)[:10]
    y = 180
    for line in lines:
        draw.text((48, y), line, fill=(245, 247, 250), font=font_body)
        y += 44
    draw.text((48, h - 72), "（示意卡）", fill=(170, 178, 190), font=font_body)

    import io

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return save_image_bytes(
        user_id=user_id,
        data=buf.getvalue(),
        source="persona",
        suffix="jpg",
        content_type="image/jpeg",
    )


def _load_media_bytes(user_id: str, file_id: str) -> Optional[bytes]:
    fid = (file_id or "").strip()
    if not fid:
        return None
    row = db.fetchone(
        "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
        (fid, user_id),
    )
    if row is None:
        return None
    from pathlib import Path

    p = Path(str(row["path"] or ""))
    if not p.is_file():
        return None
    try:
        return p.read_bytes()
    except Exception:
        return None


def generate_or_mood(
    *,
    user_id: str,
    caption: str,
    persona_name: str = "",
    backend: str = "",
    width: int = 768,
    height: int = 768,
    ref_file_id: str = "",
    allow_mood_fallback: bool = True,
) -> Optional[str]:
    """真生图 →（可选）氛围卡。返回 media file_id。有 ref_file_id 时走图生图。

    自拍/出镜请 allow_mood_fallback=False：失败不要发缺字紫卡。
    """
    caption = (caption or "").strip()
    if not caption:
        return None
    try:
        from .image_gen import generate_image_bytes

        settings = get_settings()
        ref = _load_media_bytes(user_id, ref_file_id)
        # 有参考图时强制 Qwen（唯一支持编辑/图生图）
        use_backend = backend
        if ref and not (use_backend or "").strip():
            use_backend = "qwen"
        raw = generate_image_bytes(
            caption,
            backend=use_backend,
            width=width,
            height=height,
            timeout=float(settings.image_gen_timeout_seconds or 180),
            ref_image=ref,
        )
        if raw:
            ctype = "image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            suf = "png" if ctype == "image/png" else "jpg"
            return save_image_bytes(
                user_id=user_id,
                data=raw,
                source="image_gen",
                suffix=suf,
                content_type=ctype,
            )
    except Exception:
        pass
    if not allow_mood_fallback:
        return None
    return make_mood_card(user_id=user_id, caption=caption, persona_name=persona_name)


def extract_media_intents(text: str) -> Tuple[str, list]:
    """抽出 ⟦图/表情/语音/唱/视频/一起听⟧ 等媒体暗号。"""
    intents: list = []
    out = text or ""

    def _img(m: re.Match) -> str:
        prompt = (m.group(1) or m.group(2) or "").strip()
        if prompt:
            intents.append({"kind": "image", "prompt": prompt[:200]})
        return ""

    def _sticker(m: re.Match) -> str:
        # 单张暗号也升级为表情组推荐（堆叠气泡）
        tag = (m.group(1) or m.group(2) or "").strip()
        if tag:
            intents.append({"kind": "sticker_pack", "prompt": tag[:40]})
        return ""

    def _sticker_pack(m: re.Match) -> str:
        tag = (m.group(1) or m.group(2) or "").strip()
        if tag:
            intents.append({"kind": "sticker_pack", "prompt": tag[:40]})
        return ""

    def _voice(m: re.Match) -> str:
        line = (m.group(1) or m.group(2) or "").strip()
        if line:
            intents.append({"kind": "voice", "prompt": line[:200]})
        return ""

    def _sing(m: re.Match) -> str:
        line = (m.group(1) or m.group(2) or "").strip()
        if line:
            intents.append({"kind": "sing", "prompt": line[:200]})
        return ""

    def _listen(m: re.Match) -> str:
        line = (m.group(1) or m.group(2) or (m.lastindex and m.group(m.lastindex)) or "").strip()
        # groups: 一起听 x2, 共听 x1
        for i in range(1, (m.lastindex or 0) + 1):
            g = m.group(i)
            if g is not None and str(g).strip():
                line = str(g).strip()
                break
        intents.append({"kind": "listen", "prompt": (line or "随心情")[:120]})
        return ""

    def _video(m: re.Match) -> str:
        prompt = (m.group(1) or m.group(2) or "").strip()
        if prompt:
            intents.append({"kind": "video", "prompt": prompt[:200]})
        return ""

    def _device(m: re.Match) -> str:
        raw = ""
        for i in range(1, (m.lastindex or 0) + 1):
            g = m.group(i)
            if g is not None and str(g).strip():
                raw = str(g).strip()
                break
        raw_l = raw.lower()
        kind = "android" if any(k in raw_l for k in ("手机", "phone", "android", "安卓")) else "desktop"
        intents.append({"kind": "device", "prompt": (raw or kind)[:80], "device_kind": kind})
        return ""

    def _nearby(m: re.Match) -> str:
        line = ""
        for i in range(1, (m.lastindex or 0) + 1):
            g = m.group(i)
            if g is not None and str(g).strip():
                line = str(g).strip()
                break
        intents.append({"kind": "nearby", "prompt": (line or "推荐")[:40]})
        return ""

    out = re.sub(r"⟦图[:：]\s*(.+?)⟧|\[\[图[:：]\s*(.+?)\]\]", _img, out, flags=re.S)
    # 表情组须先于单张表情匹配
    out = re.sub(
        r"⟦表情组[:：]\s*(.+?)⟧|\[\[表情组[:：]\s*(.+?)\]\]",
        _sticker_pack,
        out,
        flags=re.S,
    )
    out = re.sub(r"⟦表情[:：]\s*(.+?)⟧|\[\[表情[:：]\s*(.+?)\]\]", _sticker, out, flags=re.S)
    out = re.sub(r"⟦唱[:：]\s*(.+?)⟧|\[\[唱[:：]\s*(.+?)\]\]", _sing, out, flags=re.S)
    out = re.sub(
        r"⟦一起听[:：]\s*(.*?)⟧|\[\[一起听[:：]\s*(.*?)\]\]|⟦共听[:：]\s*(.*?)⟧",
        _listen,
        out,
        flags=re.S,
    )
    out = re.sub(r"⟦语音[:：]\s*(.+?)⟧|\[\[语音[:：]\s*(.+?)\]\]", _voice, out, flags=re.S)
    out = re.sub(r"⟦视频[:：]\s*(.+?)⟧|\[\[视频[:：]\s*(.+?)\]\]", _video, out, flags=re.S)
    out = re.sub(
        r"⟦设备[:：]\s*(.+?)⟧|\[\[设备[:：]\s*(.+?)\]\]|⟦(电脑)⟧|⟦(手机)⟧",
        _device,
        out,
        flags=re.S,
    )
    out = re.sub(
        r"⟦附近[:：]\s*(.*?)⟧|\[\[附近[:：]\s*(.*?)\]\]",
        _nearby,
        out,
        flags=re.S,
    )
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, intents


def save_audio_bytes(
    *,
    user_id: str,
    data: bytes,
    source: str = "persona_voice_reply",
    suffix: str = "wav",
    content_type: str = "audio/wav",
) -> Optional[str]:
    """落盘语音并写入 media_files，返回 file_id。"""
    if not data:
        return None
    from .media_store import put_bytes

    ext = suffix.lstrip(".") or "wav"
    fid, _ = put_bytes(
        user_id=user_id,
        data=data,
        content_type=content_type,
        source=source,
        filename=f"{ext}.{ext}",
    )
    return fid


def fixed_look_file_id(
    *,
    user_id: str,
    persona_id: str,
    allow_avatar_fallback: bool = False,
) -> Optional[str]:
    """已固定形象 file_id。

    默认不退头像：头像 ≠ 形象，出镜/自拍闸门必须真正固定 look。
    仅生成兜底时可 allow_avatar_fallback=True。
    """
    row = db.fetchone(
        """
        SELECT file_id FROM persona_assets
        WHERE user_id = ? AND persona_id = ? AND kind = 'image' AND fixed = 1
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    if row and (row["file_id"] or "").strip():
        return str(row["file_id"]).strip()
    if not allow_avatar_fallback:
        return None
    prow = db.fetchone(
        "SELECT avatar_file_id FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if prow and (prow["avatar_file_id"] or "").strip():
        return str(prow["avatar_file_id"]).strip()
    return None


def image_needs_persona_look(prompt: str) -> bool:
    """是否「发自己/人设出镜」类图（需固定形象）；纯风景不强制。"""
    p = (prompt or "").strip()
    landscape = any(
        k in p for k in ("风景", "城市", "天空", "海边", "山景", "夜景", "街景", "空镜", "房间空")
    )
    person = any(k in p for k in ("人", "她", "他", "我", "自拍", "半身", "肖像", "形象", "脸"))
    if landscape and not person:
        return False
    return True


def user_asks_persona_look(text: str) -> bool:
    """用户是否在要「你的照片/自拍/看看你」——无固定形象时应引导去抽卡。"""
    t = (text or "").strip()
    if not t:
        return False
    keys = (
        "你的照片",
        "你的自拍",
        "你自拍",
        "发一张你",
        "发张你",
        "发个你",
        "发你自拍",
        "发自拍",
        "来张自拍",
        "来个自拍",
        "看看你",
        "看下你",
        "你长啥样",
        "你长什么样",
        "你的形象",
        "自拍一张",
        "自拍给我",
        "照片给我",
        "发张照片",
        "发一张照片",
        "发个照片",
        "露个脸",
        "露脸",
        "看看长相",
        "你在干啥",
        "你在干嘛",
        "现在在干啥",
        "现在在干嘛",
    )
    if any(k in t for k in keys):
        return True
    # 「能给我发个你自拍吗」「发张你照片」等宽松口吻
    return bool(
        re.search(
            r"(你.{0,6}(自拍|照片|长相|样子|形象)|(自拍|照片).{0,6}(你|我)|看看你|看下你)",
            t,
        )
    )


def fixed_voice_prompt(*, user_id: str, persona_id: str) -> Optional[str]:
    """人设已固定音色的提示词（抽卡/上传）；无则 None。

    上传音色可能无长描述：只要已固定且有文件，仍视为可用（克隆走 wav）。
    """
    row = db.fetchone(
        """
        SELECT prompt, file_id FROM persona_assets
        WHERE user_id = ? AND persona_id = ? AND kind = 'voice' AND fixed = 1
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    if row is None:
        return None
    p = (row["prompt"] or "").strip()
    if p:
        return p
    if (row["file_id"] or "").strip():
        return "自然说话"
    return None


def fixed_voice_wav_path(*, user_id: str, persona_id: str) -> Optional[str]:
    """固定音色的试听 wav 路径（供 CosyVoice prompt_wav 克隆声线）。"""
    from pathlib import Path

    row = db.fetchone(
        """
        SELECT path FROM voice_profiles
        WHERE user_id = ? AND persona_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    if row and (row["path"] or "").strip():
        p = Path(str(row["path"]))
        if p.is_file():
            return str(p.resolve())
    row2 = db.fetchone(
        """
        SELECT path, file_id, meta_json FROM persona_assets
        WHERE user_id = ? AND persona_id = ? AND kind = 'voice' AND fixed = 1
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    if row2 is None:
        return None
    path = (row2["path"] or "").strip()
    if path and Path(path).is_file():
        return str(Path(path).resolve())
    fid = (row2["file_id"] or "").strip()
    if not fid:
        return None
    mf = db.fetchone(
        "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
        (fid, user_id),
    )
    if mf and (mf["path"] or "").strip() and Path(mf["path"]).is_file():
        return str(Path(mf["path"]).resolve())
    return None


def fixed_voice_style_hint(*, user_id: str, persona_id: str) -> str:
    """CosyVoice 短风格：抽卡变体描述（不塞长提示词，避免被念出来）。"""
    import json

    row = db.fetchone(
        """
        SELECT meta_json FROM persona_assets
        WHERE user_id = ? AND persona_id = ? AND kind = 'voice' AND fixed = 1
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id, persona_id),
    )
    if not row:
        return ""
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except Exception:
        meta = {}
    extra = (meta.get("variant_extra") or meta.get("variant_desc") or "").strip()
    label = (meta.get("variant") or "").strip()
    if extra:
        return extra.rstrip("。") + "。"
    if label:
        return f"{label}风格。"
    return ""


_AFFECT_TONE = {
    "joy": "愉悦偏高：语气轻快、略带笑意，句尾可微微上扬",
    "sadness": "低落偏高：语速稍慢、声音偏软沉，别过分卖惨",
    "excitement": "兴奋偏高：节奏更活一点、气息更利落，别喊",
    "worry": "担忧偏高：语气小心、放轻，带一点迟疑但不结巴",
    "annoyance": "不悦偏高：略硬、短促，傲娇或嫌弃感，别吼",
    "curiosity": "好奇偏高：上扬问询感、带一点探究，别播音",
}

# 逐句情绪标签 → CosyVoice 短 instruct（字幕/TTS 正文会剥掉标签）
_EMO_TAG_INSTRUCT = {
    "轻快": "语气轻快、略带笑意，句尾微微上扬。",
    "愉悦": "语气轻快、略带笑意，句尾微微上扬。",
    "低落": "语速稍慢、声音偏软沉，别过分卖惨。",
    "难过": "语速稍慢、声音偏软沉，别过分卖惨。",
    "兴奋": "节奏更活一点、气息更利落，别喊。",
    "好奇": "上扬问询感、带一点探究，别播音。",
    "害羞": "句尾变软、气声稍多，像靠近耳边。",
    "无奈": "略硬、短促，傲娇或嫌弃感，别吼。",
    "不悦": "略硬、短促，傲娇或嫌弃感，别吼。",
    "担忧": "语气小心、放轻，带一点迟疑但不结巴。",
    "平静": "平稳自然，像微信语音，不要播音腔。",
}

# CosyVoice3 允许透传的细粒度标记（其余方括号一律剥掉，防被念出）
_COS_Y_MARK_ALLOW = ("breath", "laughter", "cough", "clucking", "accent", "quick_breath")

_EMO_TAG_RE = re.compile(r"^\[([^\]]{1,12})\]\s*")
_BRACKET_RE = re.compile(r"\[([^\]]{1,24})\]")


def parse_emotion_tag(sentence: str) -> tuple[str, str]:
    """剥离句首情绪标签。返回 (tag_or_empty, clean_text)。"""
    raw = (sentence or "").strip()
    if not raw:
        return "", ""
    m = _EMO_TAG_RE.match(raw)
    if not m:
        return "", raw
    tag = (m.group(1) or "").strip()
    rest = raw[m.end() :].strip()
    return tag, rest or raw


def filter_cosy_marks(text: str) -> str:
    """只保留白名单标记，其它 [xxx] 全部去掉，避免念出系统词。"""
    raw = text or ""

    def _keep(m: re.Match) -> str:
        inner = (m.group(1) or "").strip().lower().replace(" ", "_")
        if inner in _COS_Y_MARK_ALLOW or inner in {x.replace("_", "") for x in _COS_Y_MARK_ALLOW}:
            return m.group(0)
        # 中文情绪标签已在句首剥过；正文里残留的也删
        return ""

    return _BRACKET_RE.sub(_keep, raw).strip()


def emotion_tag_to_instruct(tag: str, affect: Optional[dict] = None) -> str:
    """标签优先；affect 作基线/幅度微调。"""
    t = (tag or "").strip()
    base = _EMO_TAG_INSTRUCT.get(t, "")
    if not base:
        return speak_emotion_line(affect)
    # affect 偏低落时，「轻快/兴奋」收一点
    if isinstance(affect, dict) and t in ("轻快", "愉悦", "兴奋"):
        try:
            sad = float(affect.get("sadness", 35) or 35)
        except Exception:
            sad = 35.0
        if sad >= 55:
            return "语气偏克制的轻快，笑意淡一点，别太蹦。"
    return base


def build_sentence_instruct(
    *,
    engine: str,
    style_hint: str = "",
    emo_tag: str = "",
    affect: Optional[dict] = None,
) -> str:
    """逐句 instruct：固定风格短句 + 本句情绪。"""
    eng = (engine or "").strip().lower()
    emo = emotion_tag_to_instruct(emo_tag, affect) if emo_tag else speak_emotion_line(affect)
    hint = (style_hint or "").strip()
    if eng == "cosyvoice":
        if hint and emo:
            return f"{hint}{emo}"
        return hint or emo
    if hint and emo:
        return f"{hint}{emo}"
    return emo or hint


def speak_emotion_line(affect: Optional[dict] = None) -> str:
    """由 persona_affect 六维生成 Breeze 语气指令；禁止平铺直叙。"""
    base = "像跟熟人说话：有起伏、有口气，不要播音腔、不要每个字一样重、不要平铺直叙。"
    if not isinstance(affect, dict) or not affect:
        return base
    scored: list = []
    for key, phrase in _AFFECT_TONE.items():
        try:
            scored.append((float(affect.get(key, 35) or 35), phrase))
        except Exception:
            continue
    scored.sort(key=lambda x: -x[0])
    tops = [p for v, p in scored[:2] if v >= 45]
    if not tops and scored:
        # 都偏低时仍带最强一维一点味道，避免完全中性
        tops = [scored[0][1]]
    reason = str(affect.get("reason") or "").strip()
    extra = "；".join(tops)
    if reason:
        extra = f"{extra}（情境：{reason[:40]}）"
    return f"{base} 当前口气：{extra}。"


def speak_instructions(base_prompt: str, affect: Optional[dict] = None) -> str:
    """固定音色文案 + 情绪语气 → Breeze / VoiceDesign instruction（说话确定路线）。提示词优先。"""
    base = (base_prompt or "").strip()
    emo = speak_emotion_line(affect)
    if not base:
        return emo
    return f"【必须严格遵从的音色提示词】{base}。{emo} 读完整句，不要吞字、不要只念半句。"


def speak_instructions_for_engine(
    base_prompt: str,
    affect: Optional[dict] = None,
    *,
    engine: str = "",
    style_hint: str = "",
) -> str:
    """按 TTS 引擎选择 instruction。

    CosyVoice instruct2：音色来自 prompt wav，instruction 只能是短风格句；
    若塞入「必须严格遵从的音色提示词」长文案，会被模型朗读出来（循环念提示词）。
    Breeze / VoiceDesign 仍用完整抽卡文案。
    """
    eng = (engine or "").strip().lower()
    if eng == "cosyvoice":
        hint = (style_hint or "").strip()
        emo = speak_emotion_line(affect)
        if hint:
            return f"{hint}{emo}"
        return emo
    return speak_instructions(base_prompt, affect)


def recent_persona_voice_secs(
    *,
    user_id: str,
    conv_id: str,
    within_seconds: float = 45.0,
) -> bool:
    """会话里人设是否刚发过语音气泡。"""
    row = db.fetchone(
        """
        SELECT created_at FROM messages
        WHERE user_id = ? AND conv_id = ? AND mine = 0 AND kind = 'voice'
        ORDER BY seq DESC LIMIT 1
        """,
        (user_id, conv_id),
    )
    if row is None:
        return False
    try:
        from datetime import datetime, timezone

        raw = row["created_at"] or ""
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age < within_seconds
    except Exception:
        return False
