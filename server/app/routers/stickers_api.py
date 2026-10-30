"""表情包 API：分组（global/account）+ 多图 collect + 输入联想。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .. import db
from .. import stickers as stk
from ..config import get_settings
from ..media_cards import save_image_bytes
from ..msg_store import insert_message
from ..security import current_user, decode_access_token, new_id
from ..ws_hub import hub

router = APIRouter(prefix="/api/v1/stickers", tags=["stickers"])
_optional_bearer = HTTPBearer(auto_error=False)

_MAX_BYTES = 5 * 1024 * 1024
_ALLOWED_CT = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif")


def _actor(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    x_dsh_token: Optional[str] = Header(default=None, alias="X-DSH-Token"),
) -> Dict[str, Any]:
    settings = get_settings()
    raw = (creds.credentials if creds else "") or (x_dsh_token or "")
    svc = (settings.dsh_gateway_token or "").strip()
    if svc and raw and raw == svc:
        return {"id": "dsh_service", "is_service": True}
    if not raw:
        raise HTTPException(status_code=401, detail="需要登录或 DSH 服务令牌")
    payload = decode_access_token(raw)
    user = db.fetchone("SELECT * FROM users WHERE id = ?", (payload["sub"],))
    if user is None or user["is_disabled"]:
        raise HTTPException(status_code=401, detail="账号不可用")
    return {"id": user["id"], "is_service": False, "username": user["username"]}


class CollectIn(BaseModel):
    file_id: str = Field(default="", max_length=64)
    image_url: str = Field(default="", max_length=2048)
    label: str = Field(default="", max_length=64)
    emotion: str = Field(default="", max_length=32)
    owner_kind: str = Field(default="library", max_length=16)
    owner_id: str = Field(default="", max_length=64)
    user_id: str = Field(default="", max_length=64)
    pack_id: str = Field(default="", max_length=64)


class PatchIn(BaseModel):
    label: Optional[str] = Field(default=None, max_length=64)
    emotion: Optional[str] = Field(default=None, max_length=32)


class PackCreateIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=64)
    tags: str = Field(default="", max_length=256)
    scope: str = Field(default="account", max_length=16)
    user_id: str = Field(default="", max_length=64)
    conv_id: str = Field(default="", max_length=64)


class PackItemIn(BaseModel):
    file_id: str = Field(default="", max_length=64)
    image_url: str = Field(default="", max_length=2048)
    label: str = Field(default="", max_length=64)
    emotion: str = Field(default="", max_length=32)


class PackCollectIn(BaseModel):
    """多图收录：items 或 image_urls。"""
    items: List[PackItemIn] = Field(default_factory=list)
    image_urls: List[str] = Field(default_factory=list)
    emotions: List[str] = Field(default_factory=list)
    labels: List[str] = Field(default_factory=list)
    tags: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=64)
    user_id: str = Field(default="", max_length=64)
    conv_id: str = Field(default="", max_length=64)
    notify: bool = True


class SubscribeIn(BaseModel):
    pinned: bool = False


class WebCollectIn(BaseModel):
    """网络搜图收成一组。"""
    query: str = Field(..., min_length=1, max_length=64)
    title: str = Field(default="", max_length=64)
    tags: str = Field(default="", max_length=256)
    limit: int = Field(default=9, ge=1, le=24)
    pack_id: str = Field(default="", max_length=64)
    user_id: str = Field(default="", max_length=64)
    conv_id: str = Field(default="", max_length=64)
    notify: bool = True
    engine: str = Field(default="", max_length=16)


class ForkIn(BaseModel):
    title: str = Field(default="", max_length=64)


def _download_image(url: str) -> tuple[bytes, str]:
    u = (url or "").strip()
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("image_url 须为 http(s)")
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        r = client.get(u, headers={"User-Agent": "chat-app-sticker-collect/1.0"})
        r.raise_for_status()
        data = r.content or b""
        if len(data) > _MAX_BYTES:
            raise ValueError("图片过大（上限 5MB）")
        if len(data) < 32:
            raise ValueError("图片过小")
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype not in _ALLOWED_CT:
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                ctype = "image/png"
            elif data[:3] == b"\xff\xd8\xff":
                ctype = "image/jpeg"
            elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                ctype = "image/webp"
            elif data[:6] in (b"GIF87a", b"GIF89a"):
                ctype = "image/gif"
            else:
                raise ValueError(f"不支持的类型: {ctype or 'unknown'}")
        return data, ctype


def _resolve_uid(actor: Dict[str, Any], body_user_id: str = "") -> str:
    if actor.get("is_service"):
        uid = (body_user_id or "").strip()
        if not uid:
            raise HTTPException(400, "服务令牌须传 user_id")
        return uid
    return actor["id"]


def _save_url_as_file(uid: str, url: str) -> str:
    data, ctype = _download_image(url)
    suf = "png" if "png" in ctype else ("webp" if "webp" in ctype else ("gif" if "gif" in ctype else "jpg"))
    file_id = save_image_bytes(
        user_id=uid,
        data=data,
        source="sticker_url",
        suffix=suf,
        content_type=ctype,
    ) or ""
    if not file_id:
        raise HTTPException(500, "保存图片失败")
    return file_id


async def _emit_pack_added_card(
    *,
    user_id: str,
    conv_id: str,
    pack: Dict[str, Any],
    added: int,
) -> None:
    if not conv_id or added <= 0:
        return
    conv = db.fetchone(
        "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    )
    if conv is None:
        return
    persona_id = (conv["persona_id"] or "").strip() or "ai"
    subscribed = stk.is_subscribed(user_id=user_id, pack_id=pack["id"])
    card = {
        "v": 1,
        "card": "sticker_pack_added",
        "pack_id": pack["id"],
        "title": pack.get("title") or "表情包",
        "tags": pack.get("tags") or "",
        "scope": pack.get("scope") or "",
        "source": pack.get("source") or "",
        "item_count": pack.get("item_count") or added,
        "cover_file_ids": (pack.get("cover_file_ids") or [])[:9],
        "subscribed": subscribed,
        "body": f"推荐表情组「{pack.get('title') or '未命名'}」（{pack.get('item_count') or added} 张）·长按可加到我的",
    }
    text = json.dumps(card, ensure_ascii=False)
    row = insert_message(
        user_id=user_id,
        conv_id=conv_id,
        client_id=new_id("botcli_"),
        sender_id=persona_id,
        mine=False,
        kind="link",
        text=text,
        state="sent",
    )
    if not row:
        return
    payload = {
        "id": row["id"],
        "client_id": row["client_id"],
        "seq": row["seq"],
        "conv_id": row["conv_id"],
        "sender_id": row["sender_id"],
        "mine": bool(row["mine"]),
        "kind": row["kind"],
        "text": row["text"],
        "quote_id": row["quote_id"],
        "seconds": row["seconds"],
        "bytes": row["bytes"],
        "state": row["state"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "meta": card,
    }
    await hub.send_user(user_id, "message", payload)
    await hub.send_user(
        user_id,
        "sticker_pack_added",
        {
            "pack_id": pack["id"],
            "conv_id": conv_id,
            "title": pack.get("title"),
            "item_count": pack.get("item_count") or added,
        },
    )


@router.get("")
def list_stickers(
    emotion: str = "",
    q: str = "",
    pack_id: str = "",
    limit: int = 100,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    items = stk.list_stickers(
        user_id=user["id"],
        emotion=emotion,
        q=q,
        pack_id=pack_id,
        limit=limit,
    )
    return {
        "stickers": items,
        "total": stk.library_sticker_count(user_id=user["id"]),
        "scope": "account",
    }


@router.get("/summary")
def sticker_summary(
    actor: Dict[str, Any] = Depends(_actor),
    user_id: str = "",
    persona_id: str = "",
) -> Dict[str, Any]:
    uid = actor["id"]
    if actor.get("is_service"):
        uid = (user_id or "").strip()
        if not uid:
            raise HTTPException(400, "服务令牌须传 user_id")
    items = stk.library_sticker_summary(user_id=uid)
    return {
        "persona_id": (persona_id or "").strip(),
        "scope": "account",
        "total": sum(i["count"] for i in items),
        "by_label": items,
        "by_emotion": items,  # 兼容旧前端
    }


@router.post("/reocr")
def reocr_library(
    limit: int = Query(default=200, ge=1, le=500),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """批量 OCR 重标描述（把旧「开心」类情绪标签换成图上文字）。"""
    return stk.reannotate_library(user_id=user["id"], limit=limit)


@router.get("/suggest")
def suggest(
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=12, ge=1, le=24),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    items = stk.suggest_stickers(user_id=user["id"], q=q, limit=limit)
    return {"items": items, "q": q}


@router.get("/packs")
def list_packs(
    scope: str = "",
    limit: int = 50,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    packs = stk.list_packs(user_id=user["id"], scope=scope, limit=limit)
    return {"packs": packs}


@router.post("/packs")
async def create_pack(body: PackCreateIn, actor: Dict[str, Any] = Depends(_actor)) -> Dict[str, Any]:
    if actor.get("is_service"):
        scope = "global"
        source = "ai"
        uid = (body.user_id or "").strip()
        owner = uid
    else:
        scope = "account"
        source = "user"
        uid = actor["id"]
        owner = uid
        if (body.scope or "").strip().lower() == "global":
            raise HTTPException(403, "用户只能建账号组")
    try:
        pack = stk.create_pack(
            scope=scope,
            owner_user_id=owner or "",
            title=body.title,
            tags=body.tags,
            source=source,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return pack


@router.get("/packs/{pack_id}")
def get_pack(pack_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    pack = stk.get_pack(pack_id)
    if pack is None:
        raise HTTPException(404, "组不存在")
    if not stk.can_access_pack(pack=pack, user_id=user["id"]):
        raise HTTPException(403, "无权查看该组")
    items = stk.list_pack_items(pack_id=pack_id)
    return {**pack, "items": items, "subscribed": stk.is_subscribed(user_id=user["id"], pack_id=pack_id)}


@router.post("/packs/{pack_id}/collect")
async def collect_into_pack(
    pack_id: str,
    body: PackCollectIn,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    pack = stk.get_pack(pack_id)
    if pack is None:
        raise HTTPException(404, "组不存在")
    uid = _resolve_uid(actor, body.user_id)
    if not stk.can_append_pack(pack=pack, user_id=uid, is_service=bool(actor.get("is_service"))):
        raise HTTPException(403, "无权往该组追加")

    items: List[PackItemIn] = list(body.items or [])
    for i, url in enumerate(body.image_urls or []):
        emo = body.emotions[i] if i < len(body.emotions) else ""
        lab = body.labels[i] if i < len(body.labels) else ""
        items.append(PackItemIn(image_url=url, emotion=emo, label=lab))
    if not items:
        raise HTTPException(400, "需要 items 或 image_urls")

    if body.tags.strip() or body.title.strip():
        db.execute(
            """
            UPDATE sticker_packs SET tags = CASE WHEN ? != '' THEN ? ELSE tags END,
              title = CASE WHEN ? != '' THEN ? ELSE title END
            WHERE id = ?
            """,
            (
                body.tags.strip(),
                body.tags.strip()[:256],
                body.title.strip(),
                body.title.strip()[:64],
                pack_id,
            ),
        )

    added: List[Dict[str, Any]] = []
    for it in items[:24]:
        file_id = (it.file_id or "").strip()
        if not file_id:
            url = (it.image_url or "").strip()
            if not url:
                continue
            try:
                file_id = _save_url_as_file(uid, url)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, f"下载失败: {exc}") from exc
        try:
            row = stk.add_sticker(
                user_id=uid,
                file_id=file_id,
                label=it.label,
                emotion=it.emotion,
                owner_kind=stk.LIBRARY_KIND,
                owner_id=uid,
                pack_id=pack_id,
            )
            added.append(row)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    pack2 = stk.get_pack(pack_id) or pack
    fresh = sum(1 for x in added if not x.get("deduped"))
    if body.notify and body.conv_id:
        await _emit_pack_added_card(
            user_id=uid,
            conv_id=body.conv_id.strip(),
            pack=pack2,
            added=fresh or len(added),
        )
    return {"pack": pack2, "items": added, "added": fresh, "total": len(added)}


@router.post("/packs/{pack_id}/items")
async def append_pack_items(
    pack_id: str,
    body: PackCollectIn,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    """往已有组追加（与 collect 同逻辑，默认不发气泡）。"""
    body.notify = False if body.notify is True and not body.conv_id else body.notify
    if not body.conv_id:
        body.notify = False
    return await collect_into_pack(pack_id, body, actor)


@router.post("/packs/{pack_id}/subscribe")
def subscribe_pack(
    pack_id: str,
    body: SubscribeIn,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    pack = stk.get_pack(pack_id)
    if pack is None:
        raise HTTPException(404, "组不存在")
    if not stk.can_access_pack(pack=pack, user_id=user["id"]):
        raise HTTPException(403, "无权订阅")
    return stk.subscribe_pack(user_id=user["id"], pack_id=pack_id, pinned=body.pinned)


@router.post("/packs/{pack_id}/fork")
def fork_pack(
    pack_id: str,
    body: ForkIn,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """复制到账号组（我的一组）。"""
    try:
        return stk.fork_pack(user_id=user["id"], pack_id=pack_id, title=body.title)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/packs/{pack_id}")
def delete_pack(
    pack_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """删除「我的」表情组（仅 owner 的 account 组）。"""
    try:
        return stk.delete_pack(user_id=user["id"], pack_id=pack_id)
    except ValueError as exc:
        msg = str(exc)
        code = 404 if "不存在" in msg else 403
        raise HTTPException(code, msg) from exc


@router.post("/packs/web_collect")
async def web_collect_pack(
    body: WebCollectIn,
    actor: Dict[str, Any] = Depends(_actor),
) -> Dict[str, Any]:
    """Bing/DDG 搜图 → 下载 → 建组/追加 → 可选发组卡片。"""
    from ..image_search import search_image_urls

    uid = _resolve_uid(actor, body.user_id)
    q = (body.query or "").strip()
    if not q:
        raise HTTPException(400, "需要 query")
    # 多搜一些直链，下载失败时有余量
    want = max(1, min(int(body.limit), 24))
    urls = search_image_urls(
        q, limit=min(24, want * 3), engine=(body.engine or None)
    )
    if not urls:
        raise HTTPException(404, "搜图无结果")

    pack_id = (body.pack_id or "").strip()
    if pack_id:
        pack = stk.get_pack(pack_id)
        if pack is None:
            raise HTTPException(404, "组不存在")
        if not stk.can_append_pack(
            pack=pack, user_id=uid, is_service=bool(actor.get("is_service"))
        ):
            raise HTTPException(403, "无权往该组追加")
    else:
        title = (body.title or "").strip() or f"{q}表情"
        tags = (body.tags or "").strip() or q
        if actor.get("is_service"):
            pack = stk.create_pack(
                scope="global",
                owner_user_id=uid,
                title=title[:64],
                tags=tags[:256],
                source="web",
            )
        else:
            pack = stk.create_pack(
                scope="account",
                owner_user_id=uid,
                title=title[:64],
                tags=tags[:256],
                source="web",
            )
        pack_id = pack["id"]

    added: List[Dict[str, Any]] = []
    errors: List[str] = []
    for url in urls:
        if len(added) >= want:
            break
        try:
            file_id = _save_url_as_file(uid, url)
            row = stk.add_sticker(
                user_id=uid,
                file_id=file_id,
                label="",
                emotion="",
                owner_kind=stk.LIBRARY_KIND,
                owner_id=uid,
                pack_id=pack_id,
            )
            added.append(row)
        except Exception as exc:
            errors.append(f"{url[:60]}: {exc}")
            continue

    if not added:
        detail = "下载全部失败"
        if errors:
            detail = f"{detail}；{errors[0]}"
        raise HTTPException(502, detail)

    pack2 = stk.get_pack(pack_id) or pack
    fresh = sum(1 for x in added if not x.get("deduped"))
    if body.notify and body.conv_id:
        await _emit_pack_added_card(
            user_id=uid,
            conv_id=body.conv_id.strip(),
            pack=pack2,
            added=fresh or len(added),
        )
    return {
        "pack": pack2,
        "items": added,
        "added": fresh,
        "total": len(added),
        "urls": urls[:want],
        "errors": errors[:5],
        "query": q,
    }


@router.post("/collect")
async def collect(body: CollectIn, actor: Dict[str, Any] = Depends(_actor)) -> Dict[str, Any]:
    """兼容旧单张收藏：写入账号默认包或指定 pack_id。"""
    uid = _resolve_uid(actor, body.user_id)

    file_id = (body.file_id or "").strip()
    if not file_id:
        url = (body.image_url or "").strip()
        if not url:
            raise HTTPException(400, "需要 file_id 或 image_url")
        try:
            file_id = _save_url_as_file(uid, url)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"下载失败: {exc}") from exc

    pack_id = (body.pack_id or "").strip()
    if not pack_id:
        pack = stk.ensure_account_default_pack(user_id=uid)
        pack_id = pack["id"]
    else:
        pack = stk.get_pack(pack_id)
        if pack is None:
            raise HTTPException(404, "组不存在")
        if not stk.can_append_pack(pack=pack, user_id=uid, is_service=bool(actor.get("is_service"))):
            raise HTTPException(403, "无权往该组追加")

    try:
        return stk.add_sticker(
            user_id=uid,
            file_id=file_id,
            label=body.label,
            emotion=body.emotion,
            owner_kind=stk.LIBRARY_KIND,
            owner_id=uid,
            pack_id=pack_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{sticker_id}")
def patch_sticker(
    sticker_id: str,
    body: PatchIn,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    if body.label is None and body.emotion is None:
        raise HTTPException(400, "需要 label 或 emotion")
    out = stk.update_sticker(
        user_id=user["id"],
        sticker_id=sticker_id,
        label=body.label,
        emotion=body.emotion,
    )
    if out is None:
        raise HTTPException(404, "表情不存在")
    return out


@router.delete("/{sticker_id}")
def remove(sticker_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    if not stk.delete_sticker(user_id=user["id"], sticker_id=sticker_id):
        raise HTTPException(status_code=404, detail="表情不存在")
    return {"ok": True}
