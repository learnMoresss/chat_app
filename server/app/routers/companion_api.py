"""陪伴状态 API：快照 / 记忆事实 / 情绪 / 成长 / 跟进 / 看板。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .. import db
from ..companion.affect import AffectService, DIMS
from ..companion.followup import FollowupService
from ..companion.growth import GrowthService, MODES, TRAITS
from ..companion.retriever import MemoryRetriever
from ..companion.snapshot import build_companion_snapshot
from ..companion.store import ConflictError, MemoryStore
from ..config import get_settings
from ..security import current_user, decode_access_token, iso

router = APIRouter(prefix="/api/v1/companion", tags=["companion"])
_optional_bearer = HTTPBearer(auto_error=False)


def _resolve_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    x_product_user_id: Optional[str] = Header(default=None, alias="X-Product-User-Id"),
) -> Dict[str, Any]:
    """用户 JWT，或 DSH 网关令牌 + X-Product-User-Id。"""
    settings = get_settings()
    raw = (creds.credentials if creds else "") or ""
    svc = (settings.dsh_gateway_token or "").strip()
    if svc and raw and raw == svc:
        uid = (x_product_user_id or "").strip()
        if not uid:
            raise HTTPException(status_code=401, detail="服务令牌需 X-Product-User-Id")
        return {"id": uid, "is_service": True}
    if not raw:
        raise HTTPException(status_code=401, detail="需要登录")
    payload = decode_access_token(raw)
    user = db.fetchone("SELECT * FROM users WHERE id = ?", (payload["sub"],))
    if user is None or user["is_disabled"]:
        raise HTTPException(status_code=401, detail="账号不可用")
    return {"id": user["id"], "is_service": False, "username": user["username"]}


def _persona_owned(user_id: str, persona_id: str) -> None:
    row = db.fetchone(
        "SELECT id FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="人设不存在")


def _policy(user_id: str, persona_id: str) -> Dict[str, Any]:
    row = db.fetchone(
        "SELECT * FROM companion_policy WHERE user_id=? AND persona_id=?",
        (user_id, persona_id),
    )
    if row:
        return dict(row)
    now = iso()
    db.execute(
        """
        INSERT INTO companion_policy(
          user_id, persona_id, memory_policy_version, pause_memory_write, pause_memory_read, updated_at
        ) VALUES (?,?,1,0,0,?)
        """,
        (user_id, persona_id, now),
    )
    return {
        "user_id": user_id,
        "persona_id": persona_id,
        "memory_policy_version": 1,
        "pause_memory_write": 0,
        "pause_memory_read": 0,
        "updated_at": now,
    }


# ----- Snapshot / Board -----


@router.get("/personas/{persona_id}/snapshot")
def snapshot(
    persona_id: str,
    query: str = "",
    user: Dict[str, Any] = Depends(_resolve_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    pol = _policy(uid, persona_id)
    if int(pol.get("pause_memory_read") or 0):
        snap = build_companion_snapshot(uid, persona_id, query="")
        snap["memories"] = []
        snap["paused_read"] = True
        return snap
    try:
        return build_companion_snapshot(uid, persona_id, query=query)
    except KeyError:
        raise HTTPException(status_code=404, detail="人设不存在") from None


@router.get("/personas/{persona_id}/board")
def board(
    persona_id: str,
    cursor: int = Query(0, ge=0),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """看板同源 JSON：情绪 + 成长 + 记忆页 + 跟进 + 游标。"""
    uid = user["id"]
    _persona_owned(uid, persona_id)
    affect = AffectService().get_or_init(uid, persona_id)
    growth = GrowthService().get_state(uid, persona_id)
    store = MemoryStore()
    memories = store.list_active(uid, persona_id, limit=50)
    followups = FollowupService().list_open(uid, persona_id)
    events = AffectService().history(uid, persona_id, limit=30)
    pol = _policy(uid, persona_id)
    state_version = int(affect.get("state_version") or 1) * 1000 + int(growth.get("growth_epoch") or 1)
    seq = state_version
    return {
        "persona_id": persona_id,
        "seq": seq,
        "cursor": cursor,
        "has_more": False,
        "state_version": state_version,
        "affect": affect,
        "affect_events": events,
        "growth": growth,
        "memories": [
            {
                "id": m["id"],
                "text": m.get("text"),
                "status": m.get("status"),
                "mem_type": m.get("mem_type"),
                "source_message_id": m.get("source_message_id"),
                "heat": store.effective_heat(m),
                "updated_at": m.get("updated_at"),
            }
            for m in memories
        ],
        "followups": followups,
        "policy": {
            "memory_policy_version": pol.get("memory_policy_version"),
            "pause_memory_write": bool(pol.get("pause_memory_write")),
            "pause_memory_read": bool(pol.get("pause_memory_read")),
        },
    }


@router.get("/personas/{persona_id}/board.html", response_class=HTMLResponse)
def board_html(
    persona_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> str:
    """App WebView 图表页：内嵌同源 board JSON + ECharts CDN。"""
    data = board(persona_id, cursor=0, user=user)
    payload = json.dumps(data, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>陪伴看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
<style>
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif; background:#f6f7f5; color:#1a1a1a; }}
  h1 {{ font-size:18px; margin:16px 16px 8px; }}
  .sub {{ color:#666; font-size:13px; margin:0 16px 12px; }}
  #radar, #trend {{ width:100%; height:280px; }}
  .card {{ background:#fff; margin:12px; padding:12px; border-radius:10px; }}
</style>
</head>
<body>
<h1>陪伴看板</h1>
<p class="sub">只读 · 不唤醒设备 · seq=<span id="seq"></span></p>
<div class="card"><div id="radar"></div></div>
<div class="card"><div id="trend"></div></div>
<script>
const DATA = {payload};
document.getElementById('seq').textContent = DATA.seq || 0;
const a = DATA.affect || {{}};
const dims = ['joy','sadness','excitement','worry','annoyance','curiosity'];
const labels = ['愉悦','低落','兴奋','担忧','不悦','好奇'];
const radar = echarts.init(document.getElementById('radar'));
radar.setOption({{
  title: {{ text: '当前情绪', left: 'center', textStyle: {{ fontSize: 14 }} }},
  radar: {{ indicator: labels.map(n => ({{ name: n, max: 100 }})), radius: '60%' }},
  series: [{{ type: 'radar', data: [{{ value: dims.map(d => a[d] ?? 35), name: 'affect' }}] }}]
}});
const events = (DATA.affect_events || []).slice().reverse();
const trend = echarts.init(document.getElementById('trend'));
trend.setOption({{
  title: {{ text: '情绪事件（近次）', left: 'center', textStyle: {{ fontSize: 14 }} }},
  xAxis: {{ type: 'category', data: events.map((e,i) => String(i+1)) }},
  yAxis: {{ type: 'value', min: 0, max: 6 }},
  series: [{{ type: 'bar', data: events.map(() => 1), name: 'events' }}]
}});
</script>
</body>
</html>"""


# ----- Memories -----


class MemoryCreate(BaseModel):
    text: str
    mem_type: str = "fact"
    subject: str = ""
    attribute: str = ""
    source_message_id: str = ""


class MemoryCorrect(BaseModel):
    text: str
    expected_revision: Optional[int] = None


@router.get("/personas/{persona_id}/memories")
def list_memories(
    persona_id: str,
    limit: int = Query(50, ge=1, le=200),
    user: Dict[str, Any] = Depends(current_user),
) -> List[Dict[str, Any]]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    store = MemoryStore()
    rows = store.list_active(uid, persona_id, limit=limit)
    return [
        {
            "id": r["id"],
            "text": r.get("text"),
            "status": r.get("status"),
            "mem_type": r.get("mem_type"),
            "source_message_id": r.get("source_message_id"),
            "storage_revision": r.get("storage_revision"),
            "heat": store.effective_heat(r),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
    ]


@router.post("/personas/{persona_id}/memories")
def create_memory(
    persona_id: str,
    body: MemoryCreate,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    pol = _policy(uid, persona_id)
    if int(pol.get("pause_memory_write") or 0):
        raise HTTPException(status_code=403, detail="记忆写入已暂停")
    try:
        return MemoryStore().create(
            user_id=uid,
            persona_id=persona_id,
            text=body.text,
            mem_type=body.mem_type,
            subject=body.subject,
            attribute=body.attribute,
            source_message_id=body.source_message_id,
            policy_version=int(pol.get("memory_policy_version") or 0),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/personas/{persona_id}/memories/{memory_id}/correct")
def correct_memory(
    persona_id: str,
    memory_id: str,
    body: MemoryCorrect,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    pol = _policy(uid, persona_id)
    try:
        return MemoryStore().correct(
            user_id=uid,
            persona_id=persona_id,
            memory_id=memory_id,
            new_text=body.text,
            expected_revision=body.expected_revision,
            policy_version=int(pol.get("memory_policy_version") or 0),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="记忆不存在") from None
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/personas/{persona_id}/memories/{memory_id}")
def delete_memory(
    persona_id: str,
    memory_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, str]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    pol = _policy(uid, persona_id)
    ok = MemoryStore().delete_hard(
        user_id=uid,
        persona_id=persona_id,
        memory_id=memory_id,
        policy_version=int(pol.get("memory_policy_version") or 0),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": "1"}


@router.get("/personas/{persona_id}/memories/search")
def search_memories(
    persona_id: str,
    q: str = "",
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    packed, report = MemoryRetriever().retrieve_for_turn(uid, persona_id, q)
    return {
        "items": [{"id": m["id"], "text": m.get("text")} for m in packed],
        "budget": {"tokens": report.total, "limit": report.limit, "trimmed": report.trimmed},
    }


# ----- Affect -----


class AffectPropose(BaseModel):
    deltas: Dict[str, float] = Field(default_factory=dict)
    event_id: str = ""
    reason: str = ""
    expected_version: Optional[int] = None
    important: bool = False


@router.get("/personas/{persona_id}/affect")
def get_affect(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    return AffectService().get_or_init(uid, persona_id)


@router.post("/personas/{persona_id}/affect/propose")
def propose_affect(
    persona_id: str,
    body: AffectPropose,
    user: Dict[str, Any] = Depends(_resolve_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    # 仅接受 DIMS；禁止「自称开心」无校验直写
    deltas = {k: float(v) for k, v in (body.deltas or {}).items() if k in DIMS}
    try:
        return AffectService().propose(
            user_id=uid,
            persona_id=persona_id,
            deltas=deltas,
            event_id=body.event_id or "",
            reason=body.reason,
            expected_version=body.expected_version,
            important=body.important,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/personas/{persona_id}/affect/history")
def affect_history(
    persona_id: str,
    limit: int = 50,
    user: Dict[str, Any] = Depends(current_user),
) -> List[Dict[str, Any]]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    return AffectService().history(uid, persona_id, limit=limit)


# ----- Growth -----


class GrowthModeIn(BaseModel):
    mode: str
    freeze_current: bool = False


class GrowthApplyIn(BaseModel):
    trait_deltas: Dict[str, float] = Field(default_factory=dict)
    evidence_ids: List[str] = Field(default_factory=list)
    note: str = ""
    epoch: Optional[int] = None


class GrowthRollbackIn(BaseModel):
    revision_id: str = ""


@router.get("/personas/{persona_id}/growth")
def get_growth(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    return GrowthService().get_state(uid, persona_id)


@router.post("/personas/{persona_id}/growth/mode")
def set_growth_mode(
    persona_id: str,
    body: GrowthModeIn,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail=f"mode 须为 {MODES}")
    return GrowthService().set_mode(uid, persona_id, body.mode, freeze_current=body.freeze_current)


@router.post("/personas/{persona_id}/growth/apply")
def apply_growth(
    persona_id: str,
    body: GrowthApplyIn,
    user: Dict[str, Any] = Depends(_resolve_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    deltas = {k: v for k, v in (body.trait_deltas or {}).items() if k in TRAITS}
    try:
        return GrowthService().apply_candidate(
            user_id=uid,
            persona_id=persona_id,
            trait_deltas=deltas,
            evidence_ids=body.evidence_ids,
            note=body.note,
            epoch=body.epoch,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/personas/{persona_id}/growth/rollback")
def rollback_growth(
    persona_id: str,
    body: GrowthRollbackIn,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    try:
        return GrowthService().rollback(uid, persona_id, body.revision_id or "")
    except KeyError:
        raise HTTPException(status_code=404, detail="修订不存在") from None


@router.get("/personas/{persona_id}/growth/revisions")
def list_growth_revisions(
    persona_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> List[Dict[str, Any]]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    rows = db.fetchall(
        """
        SELECT id, traits_json, lesson_ids_json, note, created_at
        FROM persona_growth_revisions
        WHERE user_id=? AND persona_id=?
        ORDER BY created_at DESC LIMIT 50
        """,
        (uid, persona_id),
    )
    out = []
    for r in rows:
        try:
            traits = json.loads(r["traits_json"] or "{}")
        except Exception:
            traits = {}
        try:
            lessons = json.loads(r["lesson_ids_json"] or "[]")
        except Exception:
            lessons = []
        out.append(
            {
                "id": r["id"],
                "traits": traits,
                "lesson_ids": lessons,
                "note": r["note"],
                "created_at": r["created_at"],
            }
        )
    return out


# ----- Followups -----


class FollowupIn(BaseModel):
    reason: str
    source: str = ""
    window_start: str = ""
    window_end: str = ""
    expire_at: str = ""
    cancel_when: str = ""
    idempotency_key: str = ""


@router.get("/personas/{persona_id}/followups")
def list_followups(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    return FollowupService().list_open(uid, persona_id)


@router.post("/personas/{persona_id}/followups")
def create_followup(
    persona_id: str,
    body: FollowupIn,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    return FollowupService().create(
        user_id=uid,
        persona_id=persona_id,
        reason=body.reason,
        source=body.source,
        window_start=body.window_start,
        window_end=body.window_end,
        expire_at=body.expire_at,
        cancel_when=body.cancel_when,
        idempotency_key=body.idempotency_key,
    )


@router.post("/personas/{persona_id}/followups/{followup_id}/cancel")
def cancel_followup(
    persona_id: str,
    followup_id: str,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, str]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    if not FollowupService().cancel(uid, persona_id, followup_id):
        raise HTTPException(status_code=404, detail="跟进不存在")
    return {"ok": "1"}


# ----- Policy -----


class PolicyIn(BaseModel):
    pause_memory_write: Optional[bool] = None
    pause_memory_read: Optional[bool] = None
    bump_policy_version: bool = False


@router.get("/personas/{persona_id}/policy")
def get_policy(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    return _policy(uid, persona_id)


@router.post("/maintenance/clear-fill-memories")
def clear_fill_memories(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """清掉压测/规模填充产生的垃圾记忆（规模填充记忆…）。"""
    uid = user["id"]
    rows = db.fetchall(
        "SELECT id FROM memory_facts WHERE user_id=? AND text LIKE ?",
        (uid, "规模填充记忆%"),
    )
    ids = [r["id"] for r in rows]
    legacy = db.fetchall(
        "SELECT id FROM memories WHERE user_id=? AND text LIKE ?",
        (uid, "规模填充记忆%"),
    )
    for r in legacy:
        if r["id"] not in ids:
            ids.append(r["id"])
    for mid in ids:
        db.execute("DELETE FROM memory_facts WHERE id=? AND user_id=?", (mid, uid))
        db.execute("DELETE FROM memories WHERE id=? AND user_id=?", (mid, uid))
        try:
            db.execute("DELETE FROM memory_facts_fts WHERE memory_id=?", (mid,))
        except Exception:
            pass
    return {"ok": True, "deleted": len(ids)}


@router.put("/personas/{persona_id}/policy")
def put_policy(
    persona_id: str,
    body: PolicyIn,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    uid = user["id"]
    _persona_owned(uid, persona_id)
    pol = _policy(uid, persona_id)
    now = iso()
    pw = int(pol.get("pause_memory_write") or 0)
    pr = int(pol.get("pause_memory_read") or 0)
    ver = int(pol.get("memory_policy_version") or 1)
    if body.pause_memory_write is not None:
        pw = 1 if body.pause_memory_write else 0
    if body.pause_memory_read is not None:
        pr = 1 if body.pause_memory_read else 0
    if body.bump_policy_version:
        ver += 1
    db.execute(
        """
        UPDATE companion_policy SET pause_memory_write=?, pause_memory_read=?,
          memory_policy_version=?, updated_at=?
        WHERE user_id=? AND persona_id=?
        """,
        (pw, pr, ver, now, uid, persona_id),
    )
    return _policy(uid, persona_id)
