"""产品能力注册：系统有才注册；用户授权后对话可用；没有/未开则不进提示并如实告知。

与 gateway.yaml 的 worker 插件 grants 互补：这里管聊天侧产品能力（唱歌/听歌/生图/设备等）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from . import db
from .config import get_settings
from .security import iso


@dataclass(frozen=True)
class CapDef:
    id: str
    label: str
    description: str
    # 写入人设/回合提示的片段（仅当该能力对用户「已注册」时）
    prompt_hint: str


# 产品能力目录（以系统探测结果决定是否「有」）
CATALOG: List[CapDef] = [
    CapDef(
        "sticker",
        "表情包",
        "发送/收藏表情图",
        "需要配图情绪时可写 ⟦表情：情绪标签⟧；不要用 Unicode emoji。",
    ),
    CapDef(
        "image",
        "生图",
        "实景/人设图生成",
        "偶尔可写 ⟦图：画面一句话⟧ 发实景图。",
    ),
    CapDef(
        "video",
        "短视频",
        "短片生成",
        "可写 ⟦视频：画面一句话⟧ 生成短片。",
    ),
    CapDef(
        "voice",
        "语音回复",
        "固定音色 TTS 语音气泡",
        "已固定音色时可偶尔写 ⟦语音：短句⟧；不要句句语音。",
    ),
    CapDef(
        "sing",
        "唱歌",
        "唱歌语音条",
        "对方要听唱时可写 ⟦唱：曲名⟧；不要句句唱。",
    ),
    CapDef(
        "listen",
        "一起听",
        "共听歌单邀请",
        "要约共听可写 ⟦一起听：心情或主题⟧；系统会抽歌单并发邀请。",
    ),
    CapDef(
        "virtual_desktop",
        "虚拟电脑",
        "隔离 docker 桌面操作",
        "可用虚拟电脑：写 ⟦设备：电脑⟧；无人设设备时发申请卡片，等对方批准后才创建，禁止默认创建。",
    ),
    CapDef(
        "virtual_phone",
        "虚拟手机",
        "redroid 虚拟机",
        "可用虚拟手机：写 ⟦设备：手机⟧；没有则申请创建等批准，一人设一台。",
    ),
    CapDef(
        "download",
        "链接下载",
        "媒体下载任务",
        "对方丢可下链接时可走下载能力（任务卡），不要假装已下完。",
    ),
    CapDef(
        "place",
        "附近查询",
        "按对方分享的位置查周边（联网总结；地图官方检索仅可选）",
        "对方只是发了位置时：用人设闲聊即可，不要写附近标记、不要假装查过周边。"
        "只有对方明确问附近有什么/推荐/好吃好玩时，才写一行 ⟦附近：短关键词⟧（如景点、美食）；"
        "系统会按该地点联网检索并总结后再回复，禁止瞎编店名。",
    ),
]

_BY_ID = {c.id: c for c in CATALOG}

# 用户要求某能力但未开通时，人设口吻短句（进对话，不装能用）
_DENY_LINES = {
    "sing": "想唱给你听，可是唱歌这边还没开通呢。",
    "listen": "想一起听，可是共听能力还没开通。",
    "image": "想画一张，可是生图还没开通。",
    "video": "想发短片，可是视频能力还没开通。",
    "voice": "想发语音，可是语音这边还没开好。",
    "sticker": "表情包能力还没开通。",
    "virtual_desktop": "虚拟电脑这边还没开通。",
    "virtual_phone": "虚拟手机这边还没开通。",
    "download": "下载能力还没开通。",
    "place": "想帮你查附近，可是位置能力还没开通。",
}


def system_available() -> Dict[str, bool]:
    """探测本机/配置实际具备的能力（没有就不注册进目录可用集）。"""
    s = get_settings()
    out: Dict[str, bool] = {
        "sticker": True,
        "image": bool(
            (s.image_gen_comfy_url or "").strip()
            or (s.image_gen_qwen_url or "").strip()
            or (s.image_gen_backend or "").strip()
        ),
        "video": True,  # 依赖生图+ffmpeg；无图后端时执行会降级，仍可登记
        "voice": bool(
            (s.tts_breeze_url or s.tts_speech_url or s.tts_customvoice_url or "").strip()
        ),
        "sing": bool(s.sing_enabled),
        "listen": True,
        "virtual_desktop": (s.desktop_backend or "mock") not in ("", "off", "disabled"),
        "virtual_phone": bool((s.android_docker_image or "").strip()),
        "download": False,
        # 附近主路径走 DSH 联网；腾讯 Key 仅增强选点页/可选 POI
        "place": bool(
            (s.tencent_map_key or "").strip()
            or (s.dsh_gateway_enabled and (s.dsh_gateway_url or "").strip() and (s.dsh_gateway_token or "").strip())
        ),
    }
    # video 跟着 image 走更稳
    if not out["image"]:
        out["video"] = False
    # gateway 插件 media.download
    try:
        from .gateway.registry import load_gateway_config

        for g in load_gateway_config().get("grants") or []:
            if g.get("capability") == "media.download":
                out["download"] = True
                break
    except Exception:
        pass
    return out


def catalog_for_admin() -> List[Dict[str, Any]]:
    avail = system_available()
    return [
        {
            "id": c.id,
            "label": c.label,
            "description": c.description,
            "system_available": bool(avail.get(c.id)),
        }
        for c in CATALOG
    ]


def _grant_row(user_id: str, cap_id: str) -> Optional[Any]:
    return db.fetchone(
        "SELECT enabled FROM user_capability_grants WHERE user_id = ? AND capability = ?",
        (user_id, cap_id),
    )


def is_enabled_for_user(user_id: str, cap_id: str, *, is_admin: bool = False) -> bool:
    """系统有 +（管理员默认开 / 子用户默认开，除非显式关掉）。"""
    if cap_id not in _BY_ID:
        return False
    if not system_available().get(cap_id):
        return False
    row = _grant_row(user_id, cap_id)
    if row is None:
        # 无行 = 默认启用（以系统已有为准）；管理员同样默认全开
        return True
    return bool(int(row["enabled"] or 0))


def enabled_ids_for_user(user_id: str, *, is_admin: bool = False) -> Set[str]:
    avail = system_available()
    out: Set[str] = set()
    for c in CATALOG:
        if not avail.get(c.id):
            continue
        if is_enabled_for_user(user_id, c.id, is_admin=is_admin):
            out.add(c.id)
    return out


def list_for_user(user_id: str, *, is_admin: bool = False) -> List[Dict[str, Any]]:
    avail = system_available()
    items = []
    for c in CATALOG:
        sys_ok = bool(avail.get(c.id))
        enabled = is_enabled_for_user(user_id, c.id, is_admin=is_admin) if sys_ok else False
        items.append(
            {
                "id": c.id,
                "label": c.label,
                "description": c.description,
                "system_available": sys_ok,
                "enabled": enabled,
                "registered": sys_ok and enabled,
            }
        )
    return items


def set_user_grants(user_id: str, grants: Dict[str, bool]) -> List[Dict[str, Any]]:
    """管理员写入子用户开关；只能改系统已有的能力。"""
    avail = system_available()
    now = iso()
    for cap_id, enabled in grants.items():
        if cap_id not in _BY_ID:
            continue
        if not avail.get(cap_id):
            continue
        db.execute(
            """
            INSERT INTO user_capability_grants(user_id, capability, enabled, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, capability) DO UPDATE SET
              enabled = excluded.enabled,
              updated_at = excluded.updated_at
            """,
            (user_id, cap_id, 1 if enabled else 0, now),
        )
    user = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (user_id,))
    return list_for_user(user_id, is_admin=bool(user and user["is_admin"]))


def prompt_hints(
    user_id: str,
    *,
    is_admin: bool = False,
    has_fixed_voice: bool = False,
    has_fixed_look: bool = False,
) -> str:
    """只注入已注册能力短暗号；目录检索留给 media_dispatch，不在此罗列文件。"""
    enabled = enabled_ids_for_user(user_id, is_admin=is_admin)
    lines: List[str] = ["【已注册能力】只用下列暗号；未列出的不要假装能做。"]
    for c in CATALOG:
        if c.id not in enabled:
            continue
        if c.id == "voice" and not has_fixed_voice:
            continue
        lines.append(f"- {c.label}：{c.prompt_hint}")
    if "image" in enabled:
        if has_fixed_look:
            lines.append(
                "【形象】已固定。对方要自拍/看你在干啥：先短回「正在拍」，"
                "再写 ⟦图：当前场景自拍一句话⟧；系统会图生图。表情包才常带字。"
            )
        else:
            lines.append(
                "【形象】未固定（头像不算）。对方要自拍/看你时不要写自拍 ⟦图⟧、不要假装已发；"
                "口语提去抽卡固定，系统会引导。"
            )
    missing = [c.label for c in CATALOG if system_available().get(c.id) and c.id not in enabled]
    if missing:
        lines.append("【未开通】" + "、".join(missing) + "。对方要时如实说还没开。")
    return "\n".join(lines)


def deny_line(cap_id: str) -> str:
    return _DENY_LINES.get(cap_id) or "这个能力还没开通呢。"


def filter_media_intents(
    intents: list,
    *,
    user_id: str,
    is_admin: bool = False,
    has_fixed_voice: bool = False,
) -> tuple[list, List[str]]:
    """去掉未注册能力的媒体意图，并返回应对用户说的短句。"""
    enabled = enabled_ids_for_user(user_id, is_admin=is_admin)
    kind_map = {
        "sticker": "sticker",
        "sticker_pack": "sticker",
        "image": "image",
        "video": "video",
        "voice": "voice",
        "sing": "sing",
        "sing_replay": "sing",
        "listen": "listen",
        "nearby": "place",
    }
    kept = []
    denies: List[str] = []
    seen_deny: Set[str] = set()
    for intent in intents:
        kind = str(intent.get("kind") or "")
        cap = kind_map.get(kind)
        if not cap:
            kept.append(intent)
            continue
        if cap not in enabled:
            if cap not in seen_deny:
                denies.append(deny_line(cap))
                seen_deny.add(cap)
            continue
        kept.append(intent)
    return kept, denies
