"""抽卡多样性：同提示词多抽时拉开差异。

形象：抽「长什么样」（发型/五官气质/穿搭/画风），不是换镜头。
必须锚定人设性别与角色身份（雪儿=女），禁止随机出男。
音色：VoiceDesign 无 seed，用指令轴拉开听感，仍锁性别。
"""
from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Tuple


_VOICE_VARIANTS: List[Dict[str, str]] = [
    {"label": "低沉慢语", "extra": "音高略低、语速偏慢、气口更明显；情绪沉静，笑意很淡。"},
    {"label": "清亮轻快", "extra": "音高略高、语速偏快、节奏跳跃；情绪活泼，笑意更明显。"},
    {"label": "软糯害羞", "extra": "句尾变软、气声稍多；情绪含蓄害羞，像靠近耳边。"},
    {"label": "利落干练", "extra": "咬字清晰有力、语速稳定；情绪自信克制，少撒娇。"},
    {"label": "慵懒低语", "extra": "语速很慢略拖腔、气声明显；情绪随意放松。"},
    {"label": "甜感上扬", "extra": "句尾上扬、尾音轻弹；情绪俏皮亲昵，笑意足。"},
]


def voice_variant_blurb(label: str, extra: str) -> str:
    """列表展示用：短特色描述。"""
    e = (extra or "").strip().rstrip("。")
    return f"{label}：{e}" if label else e


# 形象轴：外貌候选（同一角色设定下的不同长相方案），固定半身肖像构图
_IMAGE_VARIANTS: List[Dict[str, str]] = [
    {
        "label": "清冷短发",
        "ratio": "3:4",
        "extra": (
            "黑色或深棕短发、刘海整齐；五官清冷淡颜，眼神淡淡的；"
            "简约浅色上衣，气质克制疏离。"
        ),
    },
    {
        "label": "软萌长发",
        "ratio": "3:4",
        "extra": (
            "柔顺长发微卷、发梢浅色挑染可选；眼睛更大更圆，表情柔和带浅笑；"
            "浅色卫衣或针织，气质软萌亲近。"
        ),
    },
    {
        "label": "飒爽马尾",
        "ratio": "3:4",
        "extra": (
            "高马尾或侧马尾，利落有精神；眉眼更锋利一点；"
            "运动风或衬衫，气质飒爽、嘴硬心软感。"
        ),
    },
    {
        "label": "慵懒微卷",
        "ratio": "3:4",
        "extra": (
            "中长发自然微卷、略显慵懒；卧蚕浅笑，眼神懒洋洋；"
            "宽松家居/针织开衫，日常感强。"
        ),
    },
    {
        "label": "学院双马尾",
        "ratio": "3:4",
        "extra": (
            "双马尾或双束发，学院感；五官精致偏二次元；"
            "校服或白衬衫领，气质乖巧但会傲娇。"
        ),
    },
    {
        "label": "冷感直发",
        "ratio": "3:4",
        "extra": (
            "齐肩或过肩直发，发色偏冷黑；薄唇、眼神冷静；"
            "深色或莫兰迪色穿搭，高级感清冷。"
        ),
    },
]


_FEMALE_HINT = re.compile(
    r"(女|她|妹妹|姐姐|姑娘|少女|小姐|老婆|女友|娘|姐|妹|雪儿|苏糖|哥哥)",
    re.I,
)
_MALE_HINT = re.compile(r"(^\s*男|他是|弟弟角色|少年男|小伙|男友|老公)", re.I)


def infer_gender(*, name: str = "", bio: str = "", prompt: str = "") -> str:
    blob_persona = f"{name}\n{bio}"
    if _FEMALE_HINT.search(blob_persona):
        return "female"
    if _MALE_HINT.search(blob_persona) and not _FEMALE_HINT.search(blob_persona):
        return "male"
    if _FEMALE_HINT.search(prompt):
        return "female"
    if re.search(r"男声|男性|帅哥|少年", prompt):
        return "male"
    return "neutral"


def _gender_lock_text(gender: str) -> str:
    if gender == "female":
        return (
            "【硬性】必须是年轻女性角色，绝对禁止生成男性、男孩、伪男性脸。"
            "同批图都是同一人设的不同「长相方案」，性别与身份不变。"
        )
    if gender == "male":
        return (
            "【硬性】必须是年轻男性角色，绝对禁止生成女性。"
            "同批图都是同一人设的不同长相方案，性别与身份不变。"
        )
    return "【硬性】同批必须是同一角色身份，禁止无故换成另一个人或改性别。"


def _persona_anchor(*, name: str, bio: str, prompt: str) -> str:
    display = (name or "").strip() or "该角色"
    bio_one = re.sub(r"\s+", " ", (bio or "").strip())[:160]
    bits = [f"为角色「{display}」设计形象候选"]
    if bio_one:
        bits.append(f"人设：{bio_one}")
    bits.append(f"用户要求：{(prompt or '').strip()}")
    return "；".join(bits)


def _pick(variants: List[Dict[str, str]], index: int, *, shuffle_salt: int = 0) -> Dict[str, str]:
    n = len(variants)
    if n == 0:
        return {"label": "", "extra": "", "ratio": "3:4"}
    start = shuffle_salt % n
    return variants[(start + index) % n]


def voice_variant(
    base_prompt: str,
    index: int,
    *,
    salt: int | None = None,
    name: str = "",
    bio: str = "",
) -> Tuple[str, Dict[str, Any]]:
    base = (base_prompt or "").strip()
    salt = int(salt if salt is not None else random.randint(0, 10_000))
    v = _pick(_VOICE_VARIANTS, index, shuffle_salt=salt)
    gender = infer_gender(name=name, bio=bio, prompt=base)
    gender_line = ""
    if gender == "female":
        gender_line = "必须是年轻女声，禁止男声。"
    elif gender == "male":
        gender_line = "必须是年轻男声，禁止女声。"
    persona = f"角色「{(name or '').strip() or '陪伴'}」。" if (name or bio) else ""
    # 提示词绝对优先；变体只作轻微听感扰动，禁止盖过用户描述
    seed = random.randint(1, 2**31 - 1)
    instructions = (
        f"【必须严格遵从的音色提示词】{base}\n"
        f"{persona}{gender_line}"
        f"【轻微听感扰动·{v['label']}】{v['extra']}"
        "扰动不得改变提示词里的年龄、性别、口音与性格；不要念出变体名称。"
    )
    blurb = voice_variant_blurb(v["label"], v["extra"])
    return instructions, {
        "variant": v["label"],
        "variant_desc": blurb,
        "variant_extra": v["extra"],
        "variant_index": index,
        "variant_salt": salt,
        "seed": seed,
        "gender": gender,
        "engine": "breeze",
        "diversity": "timbre_axes",
        "base_prompt": base,
    }


def image_variant(
    base_prompt: str,
    index: int,
    *,
    salt: int | None = None,
    name: str = "",
    bio: str = "",
    ref_persona: bool = True,
) -> Tuple[str, str, Dict[str, Any]]:
    """抽长相方案：固定半身肖像视角，变化发型/五官气质/穿搭。"""
    base = (base_prompt or "").strip()
    salt = int(salt if salt is not None else random.randint(0, 10_000))
    v = _pick(_IMAGE_VARIANTS, index, shuffle_salt=salt)
    seed = random.randint(1, 2**31 - 1)
    gender = infer_gender(name=name, bio=bio, prompt=base)
    anchor = (
        _persona_anchor(name=name, bio=bio, prompt=base)
        if ref_persona
        else f"用户要求：{base}"
    )
    prompt = (
        f"{anchor}。"
        f"{_gender_lock_text(gender)}"
        "【画面硬性】整张图只能有 1 个人物，禁止多人、禁止角色设定表、禁止九宫格拼贴、"
        "禁止一张图里画很多小头像/分镜/对比图；主体占画面主要面积，干净单一背景。"
        "统一构图：单人半身肖像、镜头平视、自然光、动漫风格。"
        f"本张长相方案「{v['label']}」：{v['extra']}"
        "与同批其他候选在发型、五官气质、穿搭上要一眼能区分，但仍是同一人设角色。"
    )
    return prompt, str(v.get("ratio") or "3:4"), {
        "variant": v["label"],
        "variant_index": index,
        "variant_salt": salt,
        "ratio": v.get("ratio") or "3:4",
        "seed": seed,
        "gender": gender,
        "ref_persona": bool(ref_persona),
        "engine": "qwen-image-2.1",
        "diversity": "look_design",
    }
