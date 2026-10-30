from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from .. import schedules as sched
from ..security import current_user

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


class ScheduleIn(BaseModel):
    persona_id: str
    kind: str = "custom"
    title: str = Field(default="", max_length=64)
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    prompt: str = Field(min_length=1, max_length=2000)
    jitter_minutes: int = Field(default=10, ge=0, le=120)
    daily_limit: int = Field(default=1, ge=1, le=10)
    require_chatted_today: bool = False
    min_interval_minutes: int = Field(default=60, ge=1, le=1440)
    enabled: bool = True


class SchedulePatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=64)
    hour: Optional[int] = Field(default=None, ge=0, le=23)
    minute: Optional[int] = Field(default=None, ge=0, le=59)
    prompt: Optional[str] = Field(default=None, max_length=2000)
    jitter_minutes: Optional[int] = Field(default=None, ge=0, le=120)
    daily_limit: Optional[int] = Field(default=None, ge=1, le=10)
    require_chatted_today: Optional[bool] = None
    enabled: Optional[bool] = None
    paused: Optional[bool] = None
    reschedule: Optional[bool] = None


class RecipeIn(BaseModel):
    persona_id: str
    recipe: str  # greet_sleep | greet_morning


def _owned_persona(user_id: str, persona_id: str) -> Dict[str, Any]:
    row = db.fetchone(
        "SELECT id, greet, moments_on FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="人设不存在")
    return dict(row)


@router.get("/recipes")
def list_recipes() -> Dict[str, Any]:
    return {
        "recipes": [
            {
                "key": k,
                "title": v["title"],
                "kind": v["kind"],
                "hour": v["hour"],
                "minute": v["minute"],
                "prompt": v["prompt"],
            }
            for k, v in sched.RECIPES.items()
        ]
    }


@router.get("")
def list_all(
    persona_id: str = "",
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    # 打开人设定时页时先把旧钟点配方补齐并暂停，保证「旧数据」能同步看见
    if persona_id.strip():
        p = _owned_persona(user["id"], persona_id.strip())
        try:
            sched.sync_persona_proactive(
                user["id"],
                persona_id.strip(),
                greet=bool(p.get("greet", True)),
                moments_on=bool(p.get("moments_on", True)),
            )
        except Exception:
            pass
    return {"schedules": sched.list_schedules(user["id"], persona_id)}


@router.post("")
def create(body: ScheduleIn, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    _owned_persona(user["id"], body.persona_id)
    item = sched.create_schedule(
        user_id=user["id"],
        persona_id=body.persona_id,
        kind=body.kind or "custom",
        title=body.title or "定时问候",
        hour=body.hour,
        minute=body.minute,
        prompt=body.prompt,
        jitter_minutes=body.jitter_minutes,
        daily_limit=body.daily_limit,
        require_chatted_today=body.require_chatted_today,
        min_interval_minutes=body.min_interval_minutes,
        enabled=body.enabled,
    )
    return item


@router.post("/recipes")
def add_recipe(body: RecipeIn, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    _owned_persona(user["id"], body.persona_id)
    try:
        return sched.install_recipe(user["id"], body.persona_id, body.recipe)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch("/{schedule_id}")
def patch(
    schedule_id: str,
    body: SchedulePatch,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    item = sched.patch_schedule(user["id"], schedule_id, **data)
    if item is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return item


@router.delete("/{schedule_id}")
def remove(schedule_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    if not sched.delete_schedule(user["id"], schedule_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}


@router.post("/{schedule_id}/run")
async def run_now(schedule_id: str, user: Dict[str, Any] = Depends(current_user)) -> dict:
    row = db.fetchone(
        "SELECT * FROM schedules WHERE id = ? AND user_id = ?",
        (schedule_id, user["id"]),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    # 立即跑：临时把 next 设为过去
    status = await sched.fire_schedule(dict(row))
    return {"ok": True, "status": status, "schedule": sched.get_schedule(user["id"], schedule_id)}


@router.get("/runs")
def runs(
    schedule_id: str = "",
    limit: int = 30,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    return {"runs": sched.list_runs(user["id"], schedule_id, limit)}


class PauseAllIn(BaseModel):
    paused: bool = True


@router.post("/pause-all")
def pause_all(body: PauseAllIn, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    n = sched.pause_all_for_user(user["id"], paused=body.paused)
    return {"ok": True, "updated": n, "paused": body.paused}
