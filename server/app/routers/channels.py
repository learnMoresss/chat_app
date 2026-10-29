from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..crypto import decrypt_secret, encrypt_secret
from ..llm import normalize_base
from ..security import current_user, iso, new_id

router = APIRouter(prefix="/api/v1/channels", tags=["channels"])


class ChannelOut(BaseModel):
    id: str
    name: str
    model: str
    base_url: str
    key_filled: bool
    shared: bool
    owner: str  # "public" | "me" | "other"
    api_key: Optional[str] = None  # 仅在 reveal 时返回明文


class ChannelUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: Optional[str] = None  # 不传则不改 Key；传空字符串则清空
    shared: bool = False  # 仅管理员可建公共渠道


def _can_manage(row, user: Dict[str, Any]) -> bool:
    is_public = row["user_id"] is None
    if is_public:
        return bool(user.get("is_admin"))
    return row["user_id"] == user["id"] or bool(user.get("is_admin"))


def _refuse_manage(row, user: Dict[str, Any]) -> None:
    """无权管理时：别人的私有渠道一律 404（不泄密）；公共渠道非管理员 403。"""
    if row["user_id"] is not None and row["user_id"] != user["id"] and not user.get("is_admin"):
        raise HTTPException(status_code=404, detail="渠道不存在")
    raise HTTPException(status_code=403, detail="只有管理员能改公共渠道")


def _out(row, user_id: str, *, reveal: bool = False) -> ChannelOut:
    uid = row["user_id"]
    if uid is None:
        owner = "public"
    elif uid == user_id:
        owner = "me"
    else:
        owner = "other"
    plain: Optional[str] = None
    if reveal and row["key_enc"]:
        try:
            plain = decrypt_secret(row["key_enc"])
        except ValueError:
            plain = ""
    return ChannelOut(
        id=row["id"],
        name=row["name"],
        model=row["model"],
        base_url=row["base_url"],
        key_filled=bool(row["key_enc"]),
        shared=bool(row["is_shared"]) or uid is None,
        owner=owner,
        api_key=plain,
    )


def list_visible(user_id: str) -> List[ChannelOut]:
    rows = db.fetchall(
        """
        SELECT * FROM channels
        WHERE user_id IS NULL OR user_id = ?
        ORDER BY CASE WHEN user_id IS NULL THEN 1 ELSE 0 END, created_at
        """,
        (user_id,),
    )
    return [_out(r, user_id) for r in rows]


@router.get("", response_model=List[ChannelOut])
def list_channels(user: Dict[str, Any] = Depends(current_user)) -> List[ChannelOut]:
    return list_visible(user["id"])


@router.get("/{channel_id}", response_model=ChannelOut)
def get_channel(channel_id: str, user: Dict[str, Any] = Depends(current_user)) -> ChannelOut:
    """返回渠道详情，含明文 API Key（方便自查配置）。"""
    row = db.fetchone("SELECT * FROM channels WHERE id = ?", (channel_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    if row["user_id"] is not None and row["user_id"] != user["id"] and not user.get("is_admin"):
        # 公共渠道任何人可看 Key（自部署场景）；私有渠道仅本人/管理员
        raise HTTPException(status_code=404, detail="渠道不存在")
    if row["user_id"] is None or row["user_id"] == user["id"] or user.get("is_admin"):
        return _out(row, user["id"], reveal=True)
    raise HTTPException(status_code=404, detail="渠道不存在")


@router.post("", response_model=ChannelOut)
def create_channel(body: ChannelUpsert, user: Dict[str, Any] = Depends(current_user)) -> ChannelOut:
    make_public = body.shared and user.get("is_admin")
    if body.shared and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="只有管理员能添加公共渠道")
    cid = new_id("ch_")
    key_enc = encrypt_secret(body.api_key) if body.api_key else ""
    db.execute(
        """
        INSERT INTO channels(id, user_id, name, model, base_url, key_enc, is_shared, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cid,
            None if make_public else user["id"],
            body.name.strip(),
            body.model.strip(),
            body.base_url.strip(),
            key_enc,
            1 if make_public else 0,
            iso(),
        ),
    )
    row = db.fetchone("SELECT * FROM channels WHERE id = ?", (cid,))
    assert row is not None
    return _out(row, user["id"])


@router.patch("/{channel_id}", response_model=ChannelOut)
def update_channel(
    channel_id: str,
    body: ChannelUpsert,
    user: Dict[str, Any] = Depends(current_user),
) -> ChannelOut:
    row = db.fetchone("SELECT * FROM channels WHERE id = ?", (channel_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    if not _can_manage(row, user):
        _refuse_manage(row, user)

    key_enc = row["key_enc"]
    if body.api_key is not None:
        key_enc = encrypt_secret(body.api_key) if body.api_key else ""

    db.execute(
        """
        UPDATE channels SET name = ?, model = ?, base_url = ?, key_enc = ?
        WHERE id = ?
        """,
        (body.name.strip(), body.model.strip(), body.base_url.strip(), key_enc, channel_id),
    )
    row = db.fetchone("SELECT * FROM channels WHERE id = ?", (channel_id,))
    assert row is not None
    return _out(row, user["id"], reveal=True)


def _upstream_model_id(model: str) -> str:
    """渠道 model 可能是 `provider/model`（给 DSH 路由用）；直连 OpenAI 兼容口时只传右侧 model id。"""
    s = (model or "").strip()
    if "/" in s:
        left, right = s.split("/", 1)
        left, right = left.strip(), right.strip()
        # 仅剥离已知 DSH provider 前缀，避免误伤带斜杠的官方 id
        if left in ("daycard-deepseek", "local-qwen", "deepseek-official") and right:
            return right
    return s


@router.post("/{channel_id}/probe")
def probe_channel(channel_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    """发一条最短 chat/completions，显示耗时与报错原文。"""
    row = db.fetchone("SELECT * FROM channels WHERE id = ?", (channel_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    if row["user_id"] is not None and row["user_id"] != user["id"] and not user.get("is_admin"):
        raise HTTPException(status_code=404, detail="渠道不存在")
    if not row["key_enc"]:
        return {"ok": False, "ms": 0, "error": "未填写 API Key"}
    try:
        api_key = decrypt_secret(row["key_enc"])
    except ValueError as e:
        return {"ok": False, "ms": 0, "error": f"Key 解密失败: {e}"}
    url = f"{normalize_base(row['base_url'])}/chat/completions"
    body = {
        "model": _upstream_model_id(row["model"]),
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            return {
                "ok": False,
                "ms": ms,
                "status_code": r.status_code,
                "error": (r.text or "")[:800],
            }
        return {"ok": True, "ms": ms, "status_code": r.status_code, "error": ""}
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return {"ok": False, "ms": ms, "error": str(e)}


@router.delete("/{channel_id}")
def delete_channel(channel_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    row = db.fetchone("SELECT * FROM channels WHERE id = ?", (channel_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="渠道不存在")
    if not _can_manage(row, user):
        _refuse_manage(row, user)
    db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    return {"ok": True}


def resolve_channel_for_persona(user_id: str, channel_id: str):
    """返回渠道行；无 Key 时返回 None。

    未指定人设渠道时：优先日卡（8888ok / 名称含日卡），避免误落到官方订阅。
    """
    if channel_id:
        row = db.fetchone(
            "SELECT * FROM channels WHERE id = ? AND (user_id IS NULL OR user_id = ?)",
            (channel_id, user_id),
        )
        if row and row["key_enc"]:
            return row
    # 回退：用户私有已填 Key → 公共已填 Key（日卡优先）
    row = db.fetchone(
        """
        SELECT * FROM channels
        WHERE user_id = ? AND key_enc != ''
        ORDER BY
          CASE
            WHEN lower(base_url) LIKE '%8888ok%' OR name LIKE '%日卡%' THEN 0
            ELSE 1
          END,
          created_at
        LIMIT 1
        """,
        (user_id,),
    )
    if row:
        return row
    return db.fetchone(
        """
        SELECT * FROM channels
        WHERE user_id IS NULL AND key_enc != ''
        ORDER BY
          CASE
            WHEN lower(base_url) LIKE '%8888ok%' OR name LIKE '%日卡%' THEN 0
            ELSE 1
          END,
          created_at
        LIMIT 1
        """
    )
