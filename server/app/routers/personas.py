from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, memory as mem
from ..schemas import PersonaCreate, PersonaOut, PersonaPatch
from ..security import current_user, iso, new_id

router = APIRouter(prefix="/api/v1/personas", tags=["personas"])


def _persona_out(row, memories: Optional[List] = None) -> PersonaOut:
    try:
        avatar = row["avatar_file_id"] or ""
    except Exception:
        avatar = ""
    try:
        pref = (row["voice_reply_pref"] or "sometimes").strip() or "sometimes"
    except Exception:
        pref = "sometimes"
    if pref not in ("never", "sometimes", "always"):
        pref = "sometimes"
    try:
        wl = bool(int(row["worldline_enabled"]))
    except Exception:
        wl = True
    try:
        signature = (row["signature"] or "").strip()[:40]
    except Exception:
        signature = ""
    return PersonaOut(
        id=row["id"],
        name=row["name"],
        color=int(row["color"]),
        bio=row["bio"],
        channel_id=row["channel_id"] or "",
        state=row["state"],
        is_bot=bool(row["is_bot"]),
        verify=row["verify"] or "",
        remark=row["remark"] or "",
        greet=bool(row["greet"]),
        moments_on=bool(row["moments_on"]),
        worldline_enabled=wl,
        avatar_file_id=avatar,
        voice_reply_pref=pref,
        signature=signature,
        memories=memories or [],
    )

def _owned(user_id: str, persona_id: str):
    row = db.fetchone(
        "SELECT * FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="人设不存在")
    return row


def _heuristic_extract_prompts(*, name: str, bio: str) -> Dict[str, str]:
    """从人设说明抽短提示词/签名（无 LLM 时的兜底）。"""
    import re

    n = (name or "").strip() or "她"
    raw = re.sub(r"\s+", " ", (bio or "").strip())
    # 签名：取首句，去掉「你是」类指令
    sig = raw
    for sep in ("。", "！", "？", "\n", ".", "!", "?"):
        if sep in sig:
            sig = sig.split(sep)[0]
            break
    sig = re.sub(r"^(你是一个?|作为|扮演)", "", sig).strip(" ，,")
    # 朋友圈签名要短，禁止整段人设说明落成签名
    if len(sig) > 18:
        sig = sig[:18].rstrip("，, ")
    if len(sig) < 2:
        sig = f"{n}的日常"
    look = f"二次元头像，{n}，清爽正面半身"
    # 从 bio 里捞外貌关键词
    for kw in ("长发", "短发", "黑发", "金发", "眼镜", "制服", "连衣裙", "少年", "少女", "温柔", "冷淡"):
        if kw in raw and kw not in look:
            look = f"{look}，{kw}"
    voice = f"年轻女声，语速适中，语气自然，像{n}"
    if any(k in raw for k in ("少年", "男生", "男性", "他")):
        voice = f"年轻男声，语速适中，语气自然，像{n}"
    return {
        "avatar_prompt": look[:200],
        "image_prompt": f"{look}，全身站姿，干净背景"[:200],
        "voice_prompt": voice[:200],
        "signature": sig[:40],
    }


def _parse_extract_json(content: str, *, name: str, bio: str) -> Optional[Dict[str, str]]:
    import json
    import re

    m = re.search(r"\{[\s\S]*\}", (content or "").strip())
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    out = _heuristic_extract_prompts(name=name, bio=bio)
    for k in ("avatar_prompt", "image_prompt", "voice_prompt", "signature"):
        v = str(data.get(k) or "").strip()
        if v:
            out[k] = v[:200] if k != "signature" else v[:40]
    return out


def _extract_user_prompt(*, name: str, bio: str) -> str:
    return (
        f"人设名：{name}\n说明：{(bio or '')[:800]}\n"
        "根据人设用中文输出一个 JSON 对象，字段："
        "avatar_prompt(头像文生图短提示≤40字)、image_prompt(形象短提示≤60字)、"
        "voice_prompt(音色描述≤40字)、signature(朋友圈签名≤20字，不是人设指令)。"
        "只输出 JSON。"
    )


def _llm_extract_via_channel(
    *, user_id: str, channel_id: str, name: str, bio: str
) -> Optional[Dict[str, str]]:
    """用人设绑定渠道的 API Key 直打 OpenAI 兼容接口（不经 DSH）。"""
    import httpx

    from ..crypto import decrypt_secret
    from ..llm import normalize_base

    cid = (channel_id or "").strip()
    if not cid:
        return None
    row = db.fetchone(
        """
        SELECT * FROM channels
        WHERE id = ? AND (user_id = ? OR shared = 1)
        """,
        (cid, user_id),
    )
    if not row or not (row["key_enc"] or "").strip():
        return None
    try:
        api_key = decrypt_secret(row["key_enc"])
    except Exception:
        return None
    if not api_key:
        return None
    model = (row["model"] or "").strip() or "deepseek-chat"
    # DSH 路由前缀剥掉，直连上游
    if "/" in model and not model.startswith("http"):
        model = model.split("/", 1)[-1]
    url = f"{normalize_base(row['base_url'])}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _extract_user_prompt(name=name, bio=bio)}],
        "max_tokens": 220,
        "temperature": 0.3,
    }
    try:
        with httpx.Client(timeout=18.0) as client:
            r = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if r.status_code >= 400:
            return None
        content = (
            ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        )
        return _parse_extract_json(content, name=name, bio=bio)
    except Exception:
        return None


def _llm_extract_prompts(
    *, name: str, bio: str, user_id: str = "", channel_id: str = ""
) -> Optional[Dict[str, str]]:
    """抽取提示词：渠道 Key → 本机 :9292 → None（外层再启发式）。不经 DSH。"""
    import httpx

    from ..config import get_settings

    if user_id and channel_id:
        hit = _llm_extract_via_channel(
            user_id=user_id, channel_id=channel_id, name=name, bio=bio
        )
        if hit:
            return hit

    s = get_settings()
    text_base = "http://127.0.0.1:9292/v1"
    token = (s.vision_api_token or "").strip()
    model = "qwen3.8-flash-next-iq3_xxs"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _extract_user_prompt(name=name, bio=bio)}],
        "max_tokens": 220,
        "temperature": 0.3,
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.post(f"{text_base}/chat/completions", headers=headers, json=payload)
        if r.status_code >= 400:
            return None
        content = (
            ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        )
        return _parse_extract_json(content, name=name, bio=bio)
    except Exception:
        return None


@router.post("", response_model=PersonaOut)
def create_persona(body: PersonaCreate, user: Dict[str, Any] = Depends(current_user)) -> PersonaOut:
    """新建人设：进入「新的朋友」申请（pendingIn），通过后才建会话。"""
    pid = new_id("p_")
    now = iso()
    verify = (body.verify or "").strip() or "想加你为朋友"
    signature = (body.signature or "").strip()
    if not signature:
        signature = _heuristic_extract_prompts(name=body.name, bio=body.bio).get("signature") or ""
    db.execute(
        """
        INSERT INTO personas(
          id, user_id, name, color, bio, channel_id, state, is_bot, verify, remark,
          greet, moments_on, created_at, avatar_file_id, worldline_enabled, signature
        )
        VALUES (?, ?, ?, ?, ?, ?, 'pendingIn', ?, ?, '', ?, ?, ?, ?, ?, ?)
        """,
        (
            pid,
            user["id"],
            body.name.strip(),
            body.color,
            body.bio,
            body.channel_id,
            1 if body.is_bot else 0,
            verify[:120],
            1 if body.greet else 0,
            1 if body.moments_on else 0,
            now,
            body.avatar_file_id or "",
            1 if body.worldline_enabled else 0,
            signature[:40],
        ),
    )
    # pending 不建会话、不启主动任务；通过后再做
    row = _owned(user["id"], pid)
    return _persona_out(row)


@router.post("/{persona_id}/accept", response_model=PersonaOut)
def accept_persona(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> PersonaOut:
    """通过好友申请：变 friend、建会话、系统气泡。"""
    row = _owned(user["id"], persona_id)
    if (row["state"] or "") == "friend":
        return _persona_out(row)
    now = iso()
    db.execute(
        "UPDATE personas SET state = 'friend' WHERE id = ? AND user_id = ?",
        (persona_id, user["id"]),
    )
    existing = db.fetchone(
        "SELECT id FROM conversations WHERE user_id = ? AND persona_id = ?",
        (user["id"], persona_id),
    )
    if existing is None:
        conv_id = new_id("c_")
        db.execute(
            """
            INSERT INTO conversations(id, user_id, persona_id, preview, last_time, unread, pinned, muted, draft, last_seq)
            VALUES (?, ?, ?, ?, ?, 1, 0, 0, '', 0)
            """,
            (
                conv_id,
                user["id"],
                persona_id,
                "我通过了你的朋友验证请求，现在我们可以开始聊天了",
                now,
            ),
        )
        try:
            from ..msg_store import insert_message

            insert_message(
                user_id=user["id"],
                conv_id=conv_id,
                client_id=new_id("sys_"),
                sender_id="system",
                mine=False,
                kind="system",
                text="我通过了你的朋友验证请求，现在我们可以开始聊天了",
                state="sent",
            )
        except Exception:
            pass
    try:
        from ..schedules import sync_persona_proactive

        sync_persona_proactive(
            user["id"],
            persona_id,
            greet=bool(row["greet"]),
            moments_on=bool(row["moments_on"]),
        )
    except Exception:
        pass
    try:
        from .. import stickers as stk

        stk.ensure_persona_stickers(
            user_id=user["id"],
            persona_id=persona_id,
            persona_name=row["name"] or "",
        )
    except Exception:
        pass
    row = _owned(user["id"], persona_id)
    memories = [{"id": m["id"], "text": m["text"]} for m in mem.list_memories(user["id"], persona_id)]
    return _persona_out(row, memories)


@router.post("/{persona_id}/ignore")
def ignore_persona(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """忽略待通过申请：删除人设。"""
    row = _owned(user["id"], persona_id)
    if (row["state"] or "") == "friend":
        raise HTTPException(400, "已是好友，请用人设删除")
    db.execute("DELETE FROM personas WHERE id = ? AND user_id = ?", (persona_id, user["id"]))
    return {"ok": True, "id": persona_id}


class ExtractPromptsIn(BaseModel):
    name: str = Field(default="", max_length=32)
    bio: str = Field(default="", max_length=4000)


def _do_extract(
    *, name: str, bio: str, user_id: str = "", channel_id: str = ""
) -> Dict[str, str]:
    return _llm_extract_prompts(
        name=name or "她",
        bio=bio,
        user_id=user_id,
        channel_id=channel_id,
    ) or _heuristic_extract_prompts(name=name or "她", bio=bio)


@router.post("/extract-prompts")
def extract_prompts_preview(
    body: ExtractPromptsIn,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, str]:
    """创建前人设说明预览抽取（无需 persona_id）。优先用户任一已填 Key 渠道。"""
    name = (body.name or "").strip()
    bio = (body.bio or "").strip()
    if not name and not bio:
        raise HTTPException(400, "需要 name 或 bio")
    ch = db.fetchone(
        """
        SELECT id FROM channels
        WHERE (user_id = ? OR shared = 1) AND key_enc != ''
        ORDER BY shared ASC, created_at DESC LIMIT 1
        """,
        (user["id"],),
    )
    return _do_extract(
        name=name or "她",
        bio=bio,
        user_id=user["id"],
        channel_id=(ch["id"] if ch else ""),
    )


@router.post("/{persona_id}/extract-prompts")
def extract_prompts_for_persona(
    persona_id: str,
    body: Optional[ExtractPromptsIn] = None,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, str]:
    """从已有人设抽取头像/形象/音色提示词与签名（渠道 Key，不经 DSH）。"""
    row = _owned(user["id"], persona_id)
    name = (row["remark"] or row["name"] or "").strip()
    bio = (row["bio"] or "").strip()
    if body:
        if (body.name or "").strip():
            name = body.name.strip()
        if (body.bio or "").strip():
            bio = body.bio.strip()
    return _do_extract(
        name=name or "她",
        bio=bio,
        user_id=user["id"],
        channel_id=(row["channel_id"] or ""),
    )

@router.get("", response_model=List[PersonaOut])
def list_personas(user: Dict[str, Any] = Depends(current_user)) -> List[PersonaOut]:
    rows = db.fetchall(
        "SELECT * FROM personas WHERE user_id = ? ORDER BY created_at",
        (user["id"],),
    )
    out: List[PersonaOut] = []
    for r in rows:
        memories = [{"id": m["id"], "text": m["text"]} for m in mem.list_memories(user["id"], r["id"])]
        out.append(_persona_out(r, memories))
    return out


@router.get("/{persona_id}", response_model=PersonaOut)
def get_persona(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> PersonaOut:
    row = _owned(user["id"], persona_id)
    memories = [{"id": m["id"], "text": m["text"]} for m in mem.list_memories(user["id"], persona_id)]
    return _persona_out(row, memories)


@router.patch("/{persona_id}", response_model=PersonaOut)
def patch_persona(
    persona_id: str,
    body: PersonaPatch,
    user: Dict[str, Any] = Depends(current_user),
) -> PersonaOut:
    row = _owned(user["id"], persona_id)
    name = body.name if body.name is not None else row["name"]
    bio = body.bio if body.bio is not None else row["bio"]
    channel_id = body.channel_id if body.channel_id is not None else row["channel_id"]
    remark = body.remark if body.remark is not None else row["remark"]
    greet = int(body.greet) if body.greet is not None else row["greet"]
    moments_on = int(body.moments_on) if body.moments_on is not None else row["moments_on"]
    try:
        cur_wl = int(row["worldline_enabled"])
    except Exception:
        cur_wl = 1
    worldline_enabled = int(body.worldline_enabled) if body.worldline_enabled is not None else cur_wl
    color = int(body.color) if body.color is not None else int(row["color"])
    try:
        avatar = (
            body.avatar_file_id
            if body.avatar_file_id is not None
            else (row["avatar_file_id"] or "")
        )
    except Exception:
        avatar = body.avatar_file_id or ""
    try:
        cur_pref = (row["voice_reply_pref"] or "sometimes").strip() or "sometimes"
    except Exception:
        cur_pref = "sometimes"
    pref = body.voice_reply_pref if body.voice_reply_pref is not None else cur_pref
    pref = (pref or "sometimes").strip()
    if pref not in ("never", "sometimes", "always"):
        raise HTTPException(status_code=400, detail="voice_reply_pref 须为 never|sometimes|always")
    try:
        cur_sig = (row["signature"] or "").strip()
    except Exception:
        cur_sig = ""
    signature = (
        body.signature.strip()[:40]
        if body.signature is not None
        else cur_sig
    )
    if avatar:
        owned = db.fetchone(
            "SELECT id FROM media_files WHERE id = ? AND user_id = ?",
            (avatar, user["id"]),
        )
        if not owned:
            raise HTTPException(status_code=400, detail="头像文件不存在")
    if body.worldline_enabled is not None:
        try:
            from ..companion.worldline import set_worldline_enabled

            set_worldline_enabled(user["id"], persona_id, bool(worldline_enabled))
        except Exception:
            db.execute(
                "UPDATE personas SET worldline_enabled=? WHERE id=? AND user_id=?",
                (worldline_enabled, persona_id, user["id"]),
            )
    db.execute(
        """
        UPDATE personas
        SET name = ?, bio = ?, channel_id = ?, remark = ?, greet = ?, moments_on = ?, color = ?,
            avatar_file_id = ?, voice_reply_pref = ?, signature = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            name,
            bio,
            channel_id or "",
            remark or "",
            greet,
            moments_on,
            color,
            avatar or "",
            pref,
            signature or "",
            persona_id,
            user["id"],
        ),
    )
    try:
        from ..schedules import sync_persona_proactive

        sync_persona_proactive(
            user["id"],
            persona_id,
            greet=bool(greet),
            moments_on=bool(moments_on),
        )
    except Exception:
        pass
    row = _owned(user["id"], persona_id)
    memories = [{"id": m["id"], "text": m["text"]} for m in mem.list_memories(user["id"], persona_id)]
    return _persona_out(row, memories)


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class MemoryOut(BaseModel):
    id: str
    text: str
    updated_at: str = ""


@router.get("/{persona_id}/memories", response_model=List[MemoryOut])
def get_memories(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[MemoryOut]:
    _owned(user["id"], persona_id)
    return [MemoryOut(**m) for m in mem.list_memories(user["id"], persona_id)]


@router.post("/{persona_id}/memories", response_model=MemoryOut)
def create_memory(
    persona_id: str,
    body: MemoryIn,
    user: Dict[str, Any] = Depends(current_user),
) -> MemoryOut:
    _owned(user["id"], persona_id)
    try:
        item = mem.add_memory(user["id"], persona_id, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MemoryOut(**item)


@router.patch("/{persona_id}/memories/{memory_id}", response_model=MemoryOut)
def edit_memory(
    persona_id: str,
    memory_id: str,
    body: MemoryIn,
    user: Dict[str, Any] = Depends(current_user),
) -> MemoryOut:
    _owned(user["id"], persona_id)
    item = mem.update_memory(user["id"], persona_id, memory_id, body.text)
    if item is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return MemoryOut(**item)


@router.delete("/{persona_id}/memories/{memory_id}")
def remove_memory(
    persona_id: str,
    memory_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    if not mem.delete_memory(user["id"], persona_id, memory_id):
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}


@router.get("/{persona_id}/person-notes")
def get_person_notes(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[dict]:
    _owned(user["id"], persona_id)
    from ..evidence import list_person_notes

    return list_person_notes(user["id"], persona_id)


@router.post("/{persona_id}/person-notes")
def create_person_note(
    persona_id: str,
    body: MemoryIn,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    from ..evidence import add_person_note

    try:
        return add_person_note(user["id"], persona_id, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{persona_id}/person-notes/{note_id}")
def remove_person_note(
    persona_id: str,
    note_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    from ..evidence import delete_person_note

    delete_person_note(user["id"], note_id)
    return {"ok": True}


@router.get("/{persona_id}/memory-injections")
def recent_injections(
    persona_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> List[dict]:
    _owned(user["id"], persona_id)
    rows = db.fetchall(
        """
        SELECT id, evidence_json, char_count, created_at, conv_id
        FROM memory_injections
        WHERE user_id = ? AND persona_id = ?
        ORDER BY created_at DESC LIMIT 20
        """,
        (user["id"], persona_id),
    )
    out = []
    for r in rows:
        try:
            ev = json.loads(r["evidence_json"] or "[]")
        except Exception:
            ev = []
        out.append(
            {
                "id": r["id"],
                "evidence": ev,
                "char_count": r["char_count"],
                "created_at": r["created_at"],
                "conv_id": r["conv_id"],
            }
        )
    return out


@router.post("/{persona_id}/memory-nightly")
def run_memory_nightly(
    persona_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    from ..evidence import run_nightly

    return run_nightly(user["id"], persona_id)


class EraserIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    mode: str = Field(default="word", pattern="^(word|sentence|regex)$")
    enabled: bool = True


class EraserPatch(BaseModel):
    pattern: Optional[str] = Field(default=None, max_length=200)
    mode: Optional[str] = Field(default=None, pattern="^(word|sentence|regex)$")
    enabled: Optional[bool] = None


class EraserOut(BaseModel):
    id: str
    pattern: str
    mode: str
    enabled: bool
    hits: int = 0


def _eraser_out(row) -> EraserOut:
    return EraserOut(
        id=row["id"],
        pattern=row["pattern"],
        mode=row["mode"] or "word",
        enabled=bool(row["enabled"]),
        hits=int(row["hits"] or 0),
    )


@router.get("/{persona_id}/erasers", response_model=List[EraserOut])
def list_erasers(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[EraserOut]:
    _owned(user["id"], persona_id)
    rows = db.fetchall(
        """
        SELECT * FROM eraser_rules
        WHERE user_id = ? AND persona_id = ?
        ORDER BY created_at ASC
        """,
        (user["id"], persona_id),
    )
    return [_eraser_out(r) for r in rows]


@router.post("/{persona_id}/erasers", response_model=EraserOut)
def create_eraser(
    persona_id: str,
    body: EraserIn,
    user: Dict[str, Any] = Depends(current_user),
) -> EraserOut:
    _owned(user["id"], persona_id)
    rid = new_id("er_")
    now = iso()
    db.execute(
        """
        INSERT INTO eraser_rules(id, user_id, persona_id, pattern, mode, enabled, hits, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (rid, user["id"], persona_id, body.pattern.strip(), body.mode, 1 if body.enabled else 0, now),
    )
    row = db.fetchone("SELECT * FROM eraser_rules WHERE id = ?", (rid,))
    assert row is not None
    return _eraser_out(row)


@router.patch("/{persona_id}/erasers/{eraser_id}", response_model=EraserOut)
def patch_eraser(
    persona_id: str,
    eraser_id: str,
    body: EraserPatch,
    user: Dict[str, Any] = Depends(current_user),
) -> EraserOut:
    _owned(user["id"], persona_id)
    row = db.fetchone(
        "SELECT * FROM eraser_rules WHERE id = ? AND user_id = ? AND persona_id = ?",
        (eraser_id, user["id"], persona_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    pattern = body.pattern.strip() if body.pattern is not None else row["pattern"]
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern 不能为空")
    mode = body.mode if body.mode is not None else row["mode"]
    enabled = row["enabled"] if body.enabled is None else (1 if body.enabled else 0)
    db.execute(
        "UPDATE eraser_rules SET pattern = ?, mode = ?, enabled = ? WHERE id = ?",
        (pattern, mode, enabled, eraser_id),
    )
    updated = db.fetchone("SELECT * FROM eraser_rules WHERE id = ?", (eraser_id,))
    assert updated is not None
    return _eraser_out(updated)


@router.delete("/{persona_id}/erasers/{eraser_id}")
def delete_eraser(
    persona_id: str,
    eraser_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    _owned(user["id"], persona_id)
    row = db.fetchone(
        "SELECT id FROM eraser_rules WHERE id = ? AND user_id = ? AND persona_id = ?",
        (eraser_id, user["id"], persona_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    db.execute("DELETE FROM eraser_rules WHERE id = ?", (eraser_id,))
    return {"ok": True}


@router.post("/{persona_id}/open-chat")
def open_chat(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    """从通讯录发消息：复用会话；若已删聊天则清空软删标记，回到空会话。"""
    _owned(user["id"], persona_id)
    now = iso()
    row = db.fetchone(
        """
        SELECT * FROM conversations
        WHERE user_id = ? AND persona_id = ?
        ORDER BY last_time DESC LIMIT 1
        """,
        (user["id"], persona_id),
    )
    if row is None:
        cid = new_id("c_")
        db.execute(
            """
            INSERT INTO conversations(id, user_id, persona_id, preview, last_time, unread, pinned, muted, draft, last_seq)
            VALUES (?, ?, ?, '', ?, 0, 0, 0, '', 0)
            """,
            (cid, user["id"], persona_id, now),
        )
        row = db.fetchone("SELECT * FROM conversations WHERE id = ?", (cid,))
    else:
        keys = row.keys()
        deleted = (row["deleted_at"] or "").strip() if "deleted_at" in keys else ""
        hidden = int(row["hidden"] or 0) if "hidden" in keys else 0
        if deleted or hidden:
            db.execute("DELETE FROM messages WHERE user_id = ? AND conv_id = ?", (user["id"], row["id"]))
            sets = ["preview = ''", "unread = 0", "draft = ''", "last_seq = 0", "last_time = ?"]
            args: list = [now]
            if "deleted_at" in keys:
                sets.append("deleted_at = ''")
            if "hidden" in keys:
                sets.append("hidden = 0")
            db.execute(
                f"UPDATE conversations SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
                (*args, row["id"], user["id"]),
            )
            row = db.fetchone("SELECT * FROM conversations WHERE id = ?", (row["id"],))
    assert row is not None
    from ..routers.chat import _conv_out

    return {"conversation": _conv_out(row).model_dump()}


@router.delete("/{persona_id}")
def delete_persona(
    persona_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> dict:
    """删除人设：通讯录消失，并清掉对应会话、消息与陪伴记忆。"""
    uid = user["id"]
    _owned(uid, persona_id)
    convs = db.fetchall(
        "SELECT id FROM conversations WHERE user_id = ? AND persona_id = ?",
        (uid, persona_id),
    )
    for c in convs:
        db.execute(
            "DELETE FROM messages WHERE user_id = ? AND conv_id = ?",
            (uid, c["id"]),
        )
    db.execute(
        "DELETE FROM conversations WHERE user_id = ? AND persona_id = ?",
        (uid, persona_id),
    )
    db.execute("DELETE FROM memories WHERE user_id = ? AND persona_id = ?", (uid, persona_id))
    # companion / 旁路表：缺表则跳过
    for sql in (
        "DELETE FROM memory_facts_fts WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM memory_facts WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM memory_heat_events WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM memory_source_suppress WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM persona_affect WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM persona_affect_events WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM companion_followups WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM companion_policy WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM persona_growth_revisions WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM persona_growth WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM person_notes WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM memory_injections WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM memory_runs WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM eraser_rules WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM schedules WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM persona_assets WHERE user_id = ? AND persona_id = ?",
        "DELETE FROM voice_profiles WHERE user_id = ? AND persona_id = ?",
    ):
        try:
            db.execute(sql, (uid, persona_id))
        except Exception:
            pass
    db.execute("DELETE FROM personas WHERE id = ? AND user_id = ?", (persona_id, uid))
    return {"ok": True}
