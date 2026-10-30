"""抽卡：严格使用用户提示词；多抽只换 seed，不做变体抽取/文案扰动。"""
from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Tuple

# 仅供旧资产列表补全描述；抽卡路径不再使用
_VOICE_VARIANTS: List[Dict[str, str]] = []


def voice_variant_blurb(label: str, extra: str) -> str:
    """列表展示用：短特色描述。"""
    e = (extra or "").strip().rstrip("。")
    return f"{label}：{e}" if label else e


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


def _slot_seed(salt: int, index: int) -> int:
    return (int(salt) * 1_000_003 + int(index) * 97_001 + random.randint(1, 99991)) % (
        2**31 - 1
    ) or 1


def voice_variant(
    base_prompt: str,
    index: int,
    *,
    salt: int | None = None,
    name: str = "",
    bio: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """音色：指令 = 用户提示词原文；多抽只换 seed。"""
    base = (base_prompt or "").strip()
    salt = int(salt if salt is not None else random.randint(0, 10_000))
    seed = _slot_seed(salt, index)
    gender = infer_gender(name=name, bio=bio, prompt=base)
    return base, {
        "variant": "",
        "variant_desc": "",
        "variant_extra": "",
        "variant_index": index,
        "variant_salt": salt,
        "seed": seed,
        "gender": gender,
        "engine": "cosyvoice",
        "diversity": "none",
        "base_prompt": base,
    }


def image_variant(
    base_prompt: str,
    index: int,
    *,
    salt: int | None = None,
    name: str = "",
    bio: str = "",
    ref_persona: bool = False,
) -> Tuple[str, str, Dict[str, Any]]:
    """形象：生图提示词 = 用户原文；多抽只换 seed。不注入构图/性别/人设锚点。"""
    del name, bio, ref_persona  # 接口兼容，抽卡不再混入
    base = (base_prompt or "").strip()
    salt = int(salt if salt is not None else random.randint(0, 10_000))
    seed = _slot_seed(salt, index)
    return base, "3:4", {
        "variant": "",
        "variant_desc": "",
        "variant_index": index,
        "variant_salt": salt,
        "ratio": "3:4",
        "seed": seed,
        "gender": infer_gender(prompt=base),
        "ref_persona": False,
        "engine": "zimage-bf16",
        "diversity": "none",
        "base_prompt": base,
        "negative": "",
    }
