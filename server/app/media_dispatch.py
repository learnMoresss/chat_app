"""媒体统一调度：主模型只发意图 → 库内检索 topK →（可选）轻量 flash 选片 → 发送。

不把整库文件名/描述塞进主对话 prompt（progressive disclosure）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import db
from . import stickers as stk
from .security import new_id

logger = logging.getLogger("chat.media_dispatch")

_PICK_ID_RE = re.compile(r"(?:stk_|sid_|id[_:]?\s*)?([a-zA-Z0-9_-]{8,})", re.I)


@dataclass
class DispatchCtx:
    user_id: str
    conv_id: str
    persona_id: str
    display: str
    user_text: str = ""
    persona_avatar_id: str = ""
    affect_state: Dict[str, Any] = field(default_factory=dict)
    sticker_cooled: bool = False
    voice_cooled: bool = False
    voice_prompt_fixed: Optional[str] = None
    sing_enabled: bool = False
    sing_cooled: bool = False
    voice_max: int = 80
    sing_max: int = 200


def search_stickers(*, user_id: str, query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """L3 检索：表情短名单（label/OCR）。"""
    q = (query or "").strip()
    if not q:
        return []
    hits = stk.suggest_stickers(user_id=user_id, q=q, limit=limit)
    if hits:
        return hits
    # 兜底：pick_by_emotion 精确/模糊一条也放进名单
    one = stk.pick_by_emotion(user_id=user_id, emotion=q)
    return [one] if one else []


def search_tracks(*, user_id: str, query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """曲库短名单（一起听仍主要走 curate；此接口供后续点歌细化）。"""
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    rows = db.fetchall(
        """
        SELECT id, title, artist FROM music_tracks
        WHERE user_id = ?
          AND (title LIKE ? OR artist LIKE ? OR IFNULL(lyrics,'') LIKE ?)
        ORDER BY
          CASE WHEN title LIKE ? THEN 0 WHEN artist LIKE ? THEN 1 ELSE 2 END,
          updated_at DESC
        LIMIT ?
        """,
        (user_id, like, like, like, like, like, max(1, min(int(limit), 16))),
    )
    return [dict(r) for r in rows]


async def flash_pick_id(
    *,
    user_id: str,
    persona_id: str,
    persona_name: str,
    persona_bio: str,
    intent: str,
    candidates: List[Dict[str, Any]],
    id_key: str = "id",
    label_keys: tuple = ("label", "title", "emotion"),
) -> Optional[str]:
    """短名单 >1 时用独立轻量会话选一个 id（不 resume 主聊天 cache）。"""
    if not candidates:
        return None
    if len(candidates) == 1:
        return str(candidates[0].get(id_key) or "") or None

    lines = []
    ids: List[str] = []
    for c in candidates[:8]:
        cid = str(c.get(id_key) or "").strip()
        if not cid:
            continue
        ids.append(cid)
        lab = ""
        for k in label_keys:
            v = (c.get(k) or "").strip() if isinstance(c.get(k), str) else ""
            if v:
                lab = v
                break
        lines.append(f"{cid}\t{lab}")
    if not lines:
        return None
    if len(ids) == 1:
        return ids[0]

    prompt = (
        f"意图：{intent}\n候选（id\\t描述）：\n"
        + "\n".join(lines)
        + "\n只回复最合适的一条 id，不要解释。"
    )
    try:
        from .dsh_gateway import dsh_complete, require_dsh
        from .llm import build_messages

        require_dsh()
        messages = build_messages(
            persona_bio=(persona_bio or "")[:400],
            persona_name=persona_name or "对方",
            history=[],
            include_memory_in_system=False,
            enabled_caps=set(),
            has_fixed_voice=False,
        )
        system = ""
        if messages and messages[0].get("role") == "system":
            system = messages[0].get("content") or ""
        light_conv = f"sys-media-pick-{persona_id}"
        raw = await dsh_complete(
            user_id=user_id,
            conversation_id=light_conv,
            text=prompt,
            system=system,
            instructions="只输出候选里的一个 id 字符串。",
            history=[],
            persona_id=persona_id,
        )
        text = (raw or "").strip()
        for cid in ids:
            if cid in text:
                return cid
        m = _PICK_ID_RE.search(text)
        if m and m.group(1) in ids:
            return m.group(1)
        # 模型乱回：取第一个
        return ids[0]
    except Exception:
        logger.exception("flash_pick_id failed")
        return ids[0]


async def resolve_sticker_file(
    *,
    ctx: DispatchCtx,
    prompt: str,
) -> tuple[str, str]:
    """检索 + 选片 → (file_id, caption)。无命中则生图入库。"""
    from .media_cards import generate_or_mood

    candidates = search_stickers(user_id=ctx.user_id, query=prompt, limit=8)
    chosen: Optional[Dict[str, Any]] = None
    if candidates:
        persona = db.fetchone(
            "SELECT bio, name FROM personas WHERE id = ? AND user_id = ?",
            (ctx.persona_id, ctx.user_id),
        )
        pick_id = await flash_pick_id(
            user_id=ctx.user_id,
            persona_id=ctx.persona_id,
            persona_name=ctx.display,
            persona_bio=(persona["bio"] if persona else "") or "",
            intent=prompt,
            candidates=candidates,
            id_key="id",
            label_keys=("label", "emotion"),
        )
        if pick_id:
            for c in candidates:
                if str(c.get("id")) == pick_id:
                    chosen = c
                    break
        if chosen is None:
            chosen = candidates[0]

    if chosen and chosen.get("file_id"):
        return str(chosen["file_id"]), str(chosen.get("label") or prompt)

    # 库无命中：生图并入库（与旧逻辑一致）
    fid = (
        generate_or_mood(
            user_id=ctx.user_id,
            caption=f"可爱表情包贴纸，主题：{prompt}，干净背景，方形构图",
            persona_name=ctx.display,
            backend="zimage",
            width=512,
            height=512,
        )
        or ""
    )
    if fid:
        try:
            stk.add_sticker(
                user_id=ctx.user_id,
                file_id=fid,
                label="",
                emotion="",
                owner_kind="library",
                owner_id=ctx.user_id,
            )
        except Exception:
            pass
    return fid, prompt


async def resolve_sticker_pack(*, ctx: DispatchCtx, prompt: str, need: int = 9) -> bool:
    """表情组：库内检索 → 网络搜图 → 生图补齐 → 发 sticker_pack_added 卡片。"""
    from .image_search import search_image_urls
    from .media_cards import generate_or_mood, save_image_bytes
    from .routers.stickers_api import _download_image, _emit_pack_added_card

    need = max(4, min(int(need), 12))
    theme = (prompt or "").strip()[:40]
    if not theme:
        return False

    candidates = search_stickers(user_id=ctx.user_id, query=theme, limit=need)
    file_ids: List[str] = []
    for c in candidates:
        fid = str(c.get("file_id") or "").strip()
        if fid and fid not in file_ids:
            file_ids.append(fid)

    # 网络补齐
    if len(file_ids) < need:
        try:
            urls = await asyncio.to_thread(
                search_image_urls, f"{theme} 表情包", limit=need - len(file_ids)
            )
        except Exception:
            logger.exception("web search for pack failed")
            urls = []
        for url in urls or []:
            if len(file_ids) >= need:
                break
            try:
                data, ctype = await asyncio.to_thread(_download_image, url)
                suf = (
                    "png"
                    if "png" in ctype
                    else ("webp" if "webp" in ctype else ("gif" if "gif" in ctype else "jpg"))
                )
                fid = (
                    save_image_bytes(
                        user_id=ctx.user_id,
                        data=data,
                        source="sticker_url",
                        suffix=suf,
                        content_type=ctype,
                    )
                    or ""
                )
                if fid and fid not in file_ids:
                    file_ids.append(fid)
            except Exception:
                continue

    # 生图补齐
    while len(file_ids) < max(4, need // 2):
        try:
            fid = (
                generate_or_mood(
                    user_id=ctx.user_id,
                    caption=f"可爱表情包贴纸，主题：{theme}，干净背景，方形构图",
                    persona_name=ctx.display,
                    backend="zimage",
                    width=512,
                    height=512,
                )
                or ""
            )
        except Exception:
            break
        if not fid:
            break
        if fid not in file_ids:
            file_ids.append(fid)
        else:
            break

    if not file_ids:
        return False

    pack = stk.create_pack(
        scope="global",
        owner_user_id=ctx.user_id,
        title=f"{theme}表情",
        tags=theme,
        source="web" if len(candidates) < need else "ai",
    )
    added = 0
    for fid in file_ids[:need]:
        try:
            row = stk.add_sticker(
                user_id=ctx.user_id,
                file_id=fid,
                label="",
                emotion="",
                owner_kind="library",
                owner_id=ctx.user_id,
                pack_id=pack["id"],
            )
            if not row.get("deduped"):
                added += 1
        except Exception:
            continue

    pack2 = stk.get_pack(pack["id"]) or pack
    if added <= 0 and not (pack2.get("item_count") or 0):
        return False
    await _emit_pack_added_card(
        user_id=ctx.user_id,
        conv_id=ctx.conv_id,
        pack=pack2,
        added=added or int(pack2.get("item_count") or len(file_ids)),
    )
    return True


async def dispatch_intents(ctx: DispatchCtx, intents: List[Dict[str, Any]]) -> DispatchCtx:
    """执行最多 3 条媒体意图（注册表风格分支）。"""
    # sticker/image 分支直接用这些符号；不可依赖其它 handler 的局部 import
    from .routers.chat import _insert_message, _set_typing
    from .ws_hub import hub

    for intent in intents[:3]:
        kind = intent.get("kind")
        prompt = str(intent.get("prompt") or "").strip()
        if kind != "device" and not prompt:
            continue
        if kind == "sticker" and ctx.sticker_cooled:
            continue
        if kind == "sticker_pack" and ctx.sticker_cooled:
            continue

        if kind == "device":
            await _handle_device(ctx, intent, prompt)
            continue
        if kind == "video":
            await _handle_video(ctx, prompt)
            continue
        if kind == "listen":
            await _handle_listen(ctx, prompt)
            continue
        if kind == "nearby":
            await _handle_nearby(ctx, prompt)
            continue
        if kind == "sing":
            await _handle_sing(ctx, prompt)
            continue
        if kind == "voice":
            await _handle_voice(ctx, prompt)
            continue
        if kind == "sticker":
            await _set_typing(ctx.user_id, ctx.conv_id, True, ctx.display)
            try:
                await asyncio.sleep(0.35)
                fid, caption = await resolve_sticker_file(ctx=ctx, prompt=prompt)
                if fid:
                    ctx.sticker_cooled = True
                    payload = json.dumps(
                        {"v": 1, "files": [fid], "caption": caption},
                        ensure_ascii=False,
                    )
                    img_msg = _insert_message(
                        user_id=ctx.user_id,
                        conv_id=ctx.conv_id,
                        client_id=new_id("botcli_"),
                        sender_id=ctx.persona_id,
                        mine=False,
                        kind="image",
                        text=payload,
                        state="sent",
                    )
                    await hub.send_user(ctx.user_id, "message", img_msg.model_dump())
                else:
                    note = f"（想发表情：{prompt}）"
                    note_msg = _insert_message(
                        user_id=ctx.user_id,
                        conv_id=ctx.conv_id,
                        client_id=new_id("botcli_"),
                        sender_id=ctx.persona_id,
                        mine=False,
                        kind="text",
                        text=note,
                        state="sent",
                    )
                    await hub.send_user(ctx.user_id, "message", note_msg.model_dump())
            finally:
                await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)
            continue
        if kind == "sticker_pack":
            await _set_typing(ctx.user_id, ctx.conv_id, True, ctx.display)
            try:
                await asyncio.sleep(0.35)
                ok = await resolve_sticker_pack(ctx=ctx, prompt=prompt)
                if ok:
                    ctx.sticker_cooled = True
                else:
                    note = f"（想找一组表情：{prompt}）"
                    note_msg = _insert_message(
                        user_id=ctx.user_id,
                        conv_id=ctx.conv_id,
                        client_id=new_id("botcli_"),
                        sender_id=ctx.persona_id,
                        mine=False,
                        kind="text",
                        text=note,
                        state="sent",
                    )
                    await hub.send_user(ctx.user_id, "message", note_msg.model_dump())
            finally:
                await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)
            continue
        if kind == "image":
            await _set_typing(ctx.user_id, ctx.conv_id, True, ctx.display)
            try:
                await asyncio.sleep(0.35)
                from .image_quota import check_can_generate

                q = check_can_generate(ctx.user_id)
                if not q.get("ok"):
                    note = f"（今天图发够了，明天再画：{prompt}）"
                    note_msg = _insert_message(
                        user_id=ctx.user_id,
                        conv_id=ctx.conv_id,
                        client_id=new_id("botcli_"),
                        sender_id=ctx.persona_id,
                        mine=False,
                        kind="text",
                        text=note,
                        state="sent",
                    )
                    await hub.send_user(ctx.user_id, "message", note_msg.model_dump())
                    continue
                fid = (
                    generate_or_mood(
                        user_id=ctx.user_id,
                        caption=prompt,
                        persona_name=ctx.display,
                        width=768,
                        height=768,
                        ref_file_id=ctx.persona_avatar_id,
                    )
                    or ""
                )
                if fid:
                    payload = json.dumps(
                        {"v": 1, "files": [fid], "caption": prompt},
                        ensure_ascii=False,
                    )
                    img_msg = _insert_message(
                        user_id=ctx.user_id,
                        conv_id=ctx.conv_id,
                        client_id=new_id("botcli_"),
                        sender_id=ctx.persona_id,
                        mine=False,
                        kind="image",
                        text=payload,
                        state="sent",
                    )
                    await hub.send_user(ctx.user_id, "message", img_msg.model_dump())
                else:
                    note = f"（想发图：{prompt}）"
                    note_msg = _insert_message(
                        user_id=ctx.user_id,
                        conv_id=ctx.conv_id,
                        client_id=new_id("botcli_"),
                        sender_id=ctx.persona_id,
                        mine=False,
                        kind="text",
                        text=note,
                        state="sent",
                    )
                    await hub.send_user(ctx.user_id, "message", note_msg.model_dump())
            finally:
                await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)
            continue
    return ctx


async def _handle_device(ctx: DispatchCtx, intent: Dict[str, Any], prompt: str) -> None:
    try:
        from . import virtual_devices as vdev
        from .product_capabilities import is_enabled_for_user

        dk = str(intent.get("device_kind") or "desktop")
        need = "virtual_phone" if dk == "android" else "virtual_desktop"
        urow = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (ctx.user_id,))
        if not is_enabled_for_user(
            ctx.user_id, need, is_admin=bool(urow and urow["is_admin"])
        ):
            return
        found = vdev.find_persona_device(
            ctx.user_id, persona_id=ctx.persona_id, kind=dk
        )
        if found is None:
            await vdev.emit_device_create_request(
                user_id=ctx.user_id,
                persona_id=ctx.persona_id,
                conv_id=ctx.conv_id,
                kind=dk,
                reason=prompt or "想用设备帮你操作",
            )
    except Exception:
        logger.exception("device intent failed")


async def _handle_video(ctx: DispatchCtx, prompt: str) -> None:
    from .routers.chat import _insert_message, _set_typing
    from .ws_hub import hub

    await _set_typing(ctx.user_id, ctx.conv_id, True, ctx.display)
    try:
        from .image_quota import check_can_generate
        from .video_gen import generate_short_video

        q = check_can_generate(ctx.user_id)
        if not q.get("ok"):
            raise RuntimeError("quota")
        fid = await asyncio.to_thread(
            generate_short_video,
            user_id=ctx.user_id,
            prompt=prompt,
            persona_name=ctx.display,
            ref_file_id=ctx.persona_avatar_id or "",
        )
    except Exception:
        fid = None
    if fid:
        row = db.fetchone(
            "SELECT content_type FROM media_files WHERE id=? AND user_id=?",
            (fid, ctx.user_id),
        )
        ctype = (row["content_type"] if row else "") or ""
        is_vid = ctype.startswith("video/")
        payload = json.dumps(
            {"v": 1, "files": [fid], "caption": prompt, "seconds": 3 if is_vid else 0},
            ensure_ascii=False,
        )
        vmsg = _insert_message(
            user_id=ctx.user_id,
            conv_id=ctx.conv_id,
            client_id=new_id("botcli_"),
            sender_id=ctx.persona_id,
            mine=False,
            kind="video" if is_vid else "image",
            text=payload,
            state="sent",
            seconds=3 if is_vid else 0,
        )
        await hub.send_user(ctx.user_id, "message", vmsg.model_dump())
    else:
        fail = _insert_message(
            user_id=ctx.user_id,
            conv_id=ctx.conv_id,
            client_id=new_id("botcli_"),
            sender_id=ctx.persona_id,
            mine=False,
            kind="text",
            text="这条视频这会儿做不出来，等会儿再拍给你～",
            state="sent",
        )
        await hub.send_user(ctx.user_id, "message", fail.model_dump())
    await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)


async def _handle_nearby(ctx: DispatchCtx, prompt: str) -> None:
    """按需查周边：仅当模型写出 ⟦附近⟧。

    主路径：把地点交给 DSH 联网检索并用人设口吻总结（不依赖地图官方配额）。
    可选：若腾讯额度仍可用，把 POI 短名单塞进提示作参考；失败则忽略。
    发位置卡片本身不会走到这里。
    """
    from .routers.chat import _insert_message, _set_typing
    from .ws_hub import hub
    from . import place_tencent as place
    from .dsh_gateway import dsh_complete, dsh_enabled

    await _set_typing(ctx.user_id, ctx.conv_id, True, ctx.display)
    loc = place.latest_location_in_conv(user_id=ctx.user_id, conv_id=ctx.conv_id)
    if not loc:
        msg = _insert_message(
            user_id=ctx.user_id,
            conv_id=ctx.conv_id,
            client_id=new_id("botcli_"),
            sender_id=ctx.persona_id,
            mine=False,
            kind="text",
            text="你先发个位置给我，我才好帮你看附近呀。",
            state="sent",
        )
        await hub.send_user(ctx.user_id, "message", msg.model_dump())
        await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)
        return

    kw = (prompt or "").strip() or "好玩推荐"
    name = loc.get("name") or "对方标的位置"
    addr = loc.get("address") or ""
    lat, lng = loc.get("lat"), loc.get("lng")

    # 可选：腾讯 POI 作参考（额度满/失败不影响主路径）
    poi_hint = ""
    try:
        data = await asyncio.to_thread(
            place.nearby_pois,
            user_id=ctx.user_id,
            lat=float(lat),
            lng=float(lng),
            keyword="" if kw in ("推荐", "附近", "好玩", "好玩推荐") else kw,
            radius=2500,
            page_size=6,
        )
        items = data.get("items") or []
        if items:
            poi_hint = place.format_pois_for_persona(items, limit=6)
    except Exception:
        poi_hint = ""

    text = ""
    if dsh_enabled():
        try:
            sys = (
                f"你是「{ctx.display}」。对方分享了地理位置，正在问附近。"
                "请基于地点自行联网检索或依据可靠公开信息，总结附近值得去的点；"
                "用人设口语短答，列 3～6 条即可，可带一句话理由；不要说自己是 AI；"
                "不要编造不存在的店名；若信息不足就诚实说不确定并给大致方向。"
            )
            user_q = (
                f"地点名称：{name}\n"
                f"地址：{addr or '（无）'}\n"
                f"坐标：{lat},{lng}\n"
                f"对方想了解：{kw}\n"
            )
            if poi_hint:
                user_q += f"\n（可选参考，来自地图检索，可能不全，请交叉核实）\n{poi_hint}\n"
            user_q += "\n请直接给出推荐回复正文。"
            text = await dsh_complete(
                user_id=ctx.user_id,
                conversation_id=ctx.conv_id,
                text=user_q,
                system=sys,
                instructions="nearby_place_lookup",
                persona_id=ctx.persona_id,
            )
        except Exception:
            logger.exception("nearby dsh failed")
            text = ""

    if not (text or "").strip():
        if poi_hint:
            text = (
                f"我围着「{name}」看了看（{kw}）：\n{poi_hint}\n"
                "想去哪家跟我说，我再细讲。"
            )
        else:
            text = (
                f"「{name}」这附近我这会儿没搜到靠谱清单。"
                "你再说具体点想吃的/玩的，我再帮你想想。"
            )

    out = _insert_message(
        user_id=ctx.user_id,
        conv_id=ctx.conv_id,
        client_id=new_id("botcli_"),
        sender_id=ctx.persona_id,
        mine=False,
        kind="text",
        text=text.strip(),
        state="sent",
    )
    await hub.send_user(ctx.user_id, "message", out.model_dump())
    await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)


async def _handle_listen(ctx: DispatchCtx, prompt: str) -> None:
    from .routers.chat import _insert_message, _set_typing
    from .ws_hub import hub

    await _set_typing(ctx.user_id, ctx.conv_id, True, ctx.display)
    try:
        from .routers import listen_api as _listen

        inv = await _listen.create_listen_invite(
            ctx.user_id,
            _listen.InviteBody(
                conv_id=ctx.conv_id,
                mood=prompt or "随心情",
                reason=f"想和你一起听「{prompt or '随心情'}」",
                context=ctx.user_text or "",
            ),
            push_ws=True,
        )
        n = len(inv.get("tracks") or [])
        mood = prompt or "随心情"
        card = json.dumps(
            {
                "v": 1,
                "card": "listen_invite",
                "session_id": (inv.get("session") or {}).get("id"),
                "mood": mood,
                "track_count": n,
                "body": f"一起听 · {mood}（{n} 首）",
            },
            ensure_ascii=False,
        )
        listen_msg = _insert_message(
            user_id=ctx.user_id,
            conv_id=ctx.conv_id,
            client_id=new_id("botcli_"),
            sender_id=ctx.persona_id,
            mine=False,
            kind="link",
            text=card,
            state="sent",
        )
        await hub.send_user(ctx.user_id, "message", listen_msg.model_dump())
    except Exception:
        fail = _insert_message(
            user_id=ctx.user_id,
            conv_id=ctx.conv_id,
            client_id=new_id("botcli_"),
            sender_id=ctx.persona_id,
            mine=False,
            kind="text",
            text="歌单这会儿抽不出来，等会儿再一起听～",
            state="sent",
        )
        await hub.send_user(ctx.user_id, "message", fail.model_dump())
    await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)


async def _handle_sing(ctx: DispatchCtx, prompt: str) -> None:
    from .routers.chat import _insert_message, _set_typing
    from .ws_hub import hub
    from .media_store import save_audio_bytes

    if not ctx.sing_enabled or ctx.sing_cooled:
        return
    if len(prompt) > ctx.sing_max:
        prompt = prompt[: ctx.sing_max]
    await _set_typing(ctx.user_id, ctx.conv_id, True, ctx.display)
    await asyncio.sleep(0.2)
    try:
        from .sing_gen import synthesize_sing

        sung = await asyncio.to_thread(
            synthesize_sing,
            user_id=ctx.user_id,
            persona_id=ctx.persona_id,
            prompt=prompt,
        )
    except Exception:
        sung = None
    if sung and sung.get("bytes"):
        fid = (
            save_audio_bytes(
                user_id=ctx.user_id,
                data=sung["bytes"],
                source="persona_sing",
                suffix=str(sung.get("suffix") or "mp3"),
                content_type=str(sung.get("content_type") or "audio/mpeg"),
            )
            or ""
        )
        secs = int(sung.get("seconds") or 0)
        if fid:
            payload_obj = {
                "v": 1,
                "files": [fid],
                "caption": prompt,
                "asr": prompt,
                "seconds": secs,
                "kind_extra": "sing",
                "sing": (sung.get("meta") or {}).get("sing") or {},
            }
            voice_msg = _insert_message(
                user_id=ctx.user_id,
                conv_id=ctx.conv_id,
                client_id=new_id("botcli_"),
                sender_id=ctx.persona_id,
                mine=False,
                kind="voice",
                text=json.dumps(payload_obj, ensure_ascii=False),
                state="sent",
                seconds=secs,
            )
            await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)
            await hub.send_user(ctx.user_id, "message", voice_msg.model_dump())
            ctx.sing_cooled = True
            ctx.voice_cooled = True
            return
    fail_msg = _insert_message(
        user_id=ctx.user_id,
        conv_id=ctx.conv_id,
        client_id=new_id("botcli_"),
        sender_id=ctx.persona_id,
        mine=False,
        kind="text",
        text="这首暂时唱不好，等会儿再试～",
        state="sent",
    )
    await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)
    await hub.send_user(ctx.user_id, "message", fail_msg.model_dump())


async def _handle_voice(ctx: DispatchCtx, prompt: str) -> None:
    from .routers.chat import _insert_message, _set_typing
    from .tts_gen import speak_instructions
    from .ws_hub import hub
    from .media_store import save_audio_bytes

    if ctx.voice_cooled or not ctx.voice_prompt_fixed:
        return
    if len(prompt) > ctx.voice_max:
        long_msg = _insert_message(
            user_id=ctx.user_id,
            conv_id=ctx.conv_id,
            client_id=new_id("botcli_"),
            sender_id=ctx.persona_id,
            mine=False,
            kind="text",
            text=prompt,
            state="sent",
        )
        await hub.send_user(ctx.user_id, "message", long_msg.model_dump())
        return
    await _set_typing(ctx.user_id, ctx.conv_id, True, ctx.display)
    await asyncio.sleep(0.25)
    try:
        from .tts_gen import synthesize_voice_wav

        speak_instr = speak_instructions(ctx.voice_prompt_fixed, ctx.affect_state)
        wav = await asyncio.to_thread(
            synthesize_voice_wav,
            text=prompt,
            instructions=speak_instr,
            seed=None,
        )
    except Exception:
        wav = None
    fid = ""
    if wav:
        fid = save_audio_bytes(user_id=ctx.user_id, data=wav) or ""
    if fid:
        payload = json.dumps(
            {
                "v": 1,
                "files": [fid],
                "caption": prompt,
                "asr": prompt,
                "seconds": 0,
            },
            ensure_ascii=False,
        )
        voice_msg = _insert_message(
            user_id=ctx.user_id,
            conv_id=ctx.conv_id,
            client_id=new_id("botcli_"),
            sender_id=ctx.persona_id,
            mine=False,
            kind="voice",
            text=payload,
            state="sent",
            seconds=0,
        )
        await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)
        await hub.send_user(ctx.user_id, "message", voice_msg.model_dump())
        ctx.voice_cooled = True
        return
    fail_msg = _insert_message(
        user_id=ctx.user_id,
        conv_id=ctx.conv_id,
        client_id=new_id("botcli_"),
        sender_id=ctx.persona_id,
        mine=False,
        kind="text",
        text=prompt,
        state="sent",
    )
    await _set_typing(ctx.user_id, ctx.conv_id, False, ctx.display)
    await hub.send_user(ctx.user_id, "message", fail_msg.model_dump())
