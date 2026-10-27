from __future__ import annotations

import json
from typing import Any, Dict, Optional

import httpx

from .. import db
from ..security import iso, new_id
from .. import msg_store
from .lifecycle import ensure_local_instance, mark_busy, touch_instance
from .notify import emit_job_event, emit_message_event, job_payload
from .registry import load_gateway_config, user_may_use


def _sync_job_message(job: Dict[str, Any], *, result: Any = None) -> Optional[Dict[str, Any]]:
    """任务状态变化时更新已落库的聊天消息，并推 WS message。"""
    mid = (job.get("message_id") or "").strip()
    if not mid:
        return None
    try:
        inp = json.loads(job.get("input_json") or "{}")
    except Exception:
        inp = {}
    status = job.get("status") or "queued"
    res = result
    if res is None:
        try:
            res = json.loads(job.get("result_json") or "{}")
        except Exception:
            res = {}
    kind, text, seconds, bytes_ = msg_store.job_card_text(
        capability=job.get("capability") or "",
        input_data=inp if isinstance(inp, dict) else {},
        status=status,
        result=res,
        error=job.get("error") or "",
    )
    state = "sending" if status in ("queued", "running") else ("failed" if status == "failed" else "sent")
    # 进行中把进度写进预览文案
    if status in ("queued", "running"):
        pct = int(job.get("progress_pct") or 0)
        url = str((inp or {}).get("url") or "")
        text = f"{url or text} · {pct}%" if pct else text
    row = msg_store.update_message(
        mid,
        text=text,
        kind=kind,
        state=state,
        seconds=seconds,
        bytes_=bytes_,
    )
    if row:
        pub = msg_store.row_to_public(row)
        emit_message_event(job["user_id"], pub)
        return pub
    return None


def _attach_job_message(
    *,
    user_id: str,
    job_id: str,
    conversation_id: str,
    capability: str,
    input_data: Dict[str, Any],
    client_msg_id: str = "",
) -> str:
    """创建任务时落一条聊天消息，返回 message_id。"""
    if not conversation_id:
        return ""
    kind, preview, seconds, bytes_ = msg_store.job_card_text(
        capability=capability,
        input_data=input_data,
        status="queued",
    )
    payload = {
        "v": 1,
        "job_id": job_id,
        "capability": capability,
        "url": input_data.get("url") or "",
        "action": input_data.get("action") or "",
        "status": "queued",
        "preview": preview,
    }
    row = msg_store.insert_message(
        user_id=user_id,
        conv_id=conversation_id,
        client_id=client_msg_id or new_id("jobcli_"),
        sender_id=user_id,
        mine=True,
        kind="link",
        text=json.dumps(payload, ensure_ascii=False),
        seconds=seconds,
        bytes_=bytes_,
        state="sending",
    )
    if not row:
        return ""
    db.execute("UPDATE plugin_jobs SET message_id = ? WHERE id = ?", (row["id"], job_id))
    # 会话预览用短文案
    db.execute(
        "UPDATE conversations SET preview = ? WHERE id = ? AND user_id = ?",
        (preview, conversation_id, user_id),
    )
    emit_message_event(user_id, msg_store.row_to_public(row))
    return row["id"]


def _log_plugin_usage(
    *,
    user_id: str,
    job: Dict[str, Any],
    status: str,
    error: str = "",
) -> None:
    """插件调用记入 usage_logs（client=plugin）。"""
    try:
        db.execute(
            """
            INSERT INTO usage_logs(
              id, user_id, persona_id, channel_id, conv_id, model,
              prompt_tokens, completion_tokens, total_tokens, client, status, error, created_at, estimated
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 'plugin', ?, ?, ?, 0)
            """,
            (
                new_id("ul_"),
                user_id,
                job.get("persona_id"),
                None,
                job.get("conversation_id"),
                f"{job.get('plugin_id')}:{job.get('capability')}",
                status,
                (error or "")[:500],
                iso(),
            ),
        )
    except Exception:
        pass


def _check_daily_limit(capability: str, user_id: str) -> None:
    cfg = load_gateway_config()
    limit = None
    for g in cfg.get("grants") or []:
        if g.get("capability") == capability and g.get("daily_limit") is not None:
            limit = int(g["daily_limit"])
            break
    if limit is None:
        return
    # 今日 UTC 已创建任务数（含失败，不含 interrupted 重试可另议）
    row = db.fetchone(
        """
        SELECT COUNT(*) AS c FROM plugin_jobs
        WHERE user_id = ? AND capability = ?
          AND date(created_at) = date('now')
        """,
        (user_id, capability),
    )
    if row and int(row["c"]) >= limit:
        raise PermissionError(f"今日「{capability}」已达上限 {limit} 次")


def create_job(
    *,
    user_id: str,
    is_admin: bool,
    capability: str,
    plugin_id: str,
    input_data: Dict[str, Any],
    persona_id: str = "",
    conversation_id: str = "",
    timeout_seconds: int = 120,
    upload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not user_may_use(capability, user_id=user_id, is_admin=is_admin):
        raise PermissionError("无权使用该能力")
    _check_daily_limit(capability, user_id)

    inst = ensure_local_instance(plugin_id)
    job_id = new_id("job_")
    now = iso()
    client_msg_id = str(input_data.pop("_client_msg_id", "") or "")
    db.execute(
        """
        INSERT INTO plugin_jobs (
          id, user_id, persona_id, conversation_id, worker_id, instance_id,
          plugin_id, capability, input_json, status, progress_pct, progress_msg,
          result_json, error, created_at, updated_at, message_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, '', '{}', '', ?, ?, '')
        """,
        (
            job_id,
            user_id,
            persona_id or None,
            conversation_id or None,
            inst.get("worker_id"),
            inst["id"],
            plugin_id,
            capability,
            json.dumps(input_data, ensure_ascii=False),
            now,
            now,
        ),
    )
    mark_busy(inst["id"], 1)
    touch_instance(inst["id"])

    if upload and upload.get("file_id"):
        # 结果回填 file_id 用
        input_data = {**input_data, "_upload_file_id": upload["file_id"]}
        db.execute(
            "UPDATE plugin_jobs SET input_json=? WHERE id=?",
            (json.dumps(input_data, ensure_ascii=False), job_id),
        )

    msg_id = ""
    if conversation_id:
        msg_id = _attach_job_message(
            user_id=user_id,
            job_id=job_id,
            conversation_id=conversation_id,
            capability=capability,
            input_data=input_data,
            client_msg_id=client_msg_id,
        )

    body: Dict[str, Any] = {
        "capability": capability,
        "input": input_data,
        "files": [],
        "user_id": user_id,
        "timeout_seconds": timeout_seconds,
    }
    if upload:
        body["upload"] = upload

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            r = client.post(
                f"{inst['base_url'].rstrip('/')}/jobs",
                headers={"Authorization": f"Bearer {inst['token']}"},
                json=body,
            )
            if r.status_code >= 400:
                raise RuntimeError(r.text)
            remote = r.json()
    except Exception as e:
        mark_busy(inst["id"], -1)
        db.execute(
            """
            UPDATE plugin_jobs SET status='failed', error=?, updated_at=? WHERE id=?
            """,
            (str(e), iso(), job_id),
        )
        failed = get_job(job_id, user_id=user_id)
        if failed:
            emit_job_event(user_id, "job.failed", job_payload(failed))
            row = db.fetchone("SELECT * FROM plugin_jobs WHERE id = ?", (job_id,))
            if row:
                _sync_job_message(dict(row))
        raise

    remote_job_id = remote.get("job_id") or ""
    db.execute(
        """
        UPDATE plugin_jobs
        SET status='running', remote_job_id=?, updated_at=?
        WHERE id=?
        """,
        (remote_job_id, iso(), job_id),
    )
    out = get_job(job_id, user_id=user_id)
    if out:
        emit_job_event(user_id, "job.running", job_payload(out))
        row = db.fetchone("SELECT * FROM plugin_jobs WHERE id = ?", (job_id,))
        if row:
            _sync_job_message(dict(row))
    return out  # type: ignore[return-value]


def get_job(job_id: str, *, user_id: str) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT * FROM plugin_jobs WHERE id = ? AND user_id = ?",
        (job_id, user_id),
    )
    if not row:
        return None
    return _public(dict(row))


def list_jobs(
    *,
    user_id: str,
    limit: int = 50,
    capability: Optional[str] = None,
) -> list:
    limit = max(1, min(int(limit), 200))
    if capability:
        rows = db.fetchall(
            """
            SELECT * FROM plugin_jobs
            WHERE user_id = ? AND capability = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, capability, limit),
        )
    else:
        rows = db.fetchall(
            """
            SELECT * FROM plugin_jobs
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, limit),
        )
    return [_public(dict(r)) for r in rows]


def refresh_job_from_plugin(job_id: str, *, user_id: str) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT * FROM plugin_jobs WHERE id = ? AND user_id = ?",
        (job_id, user_id),
    )
    if not row:
        return None
    job = dict(row)
    if job["status"] in ("done", "failed", "cancelled", "interrupted"):
        return _public(job)
    inst = db.fetchone(
        "SELECT * FROM plugin_instances WHERE id = ?", (job["instance_id"],)
    )
    if not inst or not job.get("remote_job_id"):
        return _public(job)
    inst = dict(inst)
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                f"{inst['base_url'].rstrip('/')}/jobs/{job['remote_job_id']}",
                headers={"Authorization": f"Bearer {inst['token']}"},
            )
            if r.status_code >= 400:
                return _public(job)
            remote = r.json()
    except Exception:
        return _public(job)

    status = remote.get("status") or job["status"]
    result = remote.get("result")
    pct = int(remote.get("progress") or job.get("progress_pct") or 0)
    if status == "running" and pct != (job.get("progress_pct") or 0):
        db.execute(
            "UPDATE plugin_jobs SET progress_pct=?, updated_at=? WHERE id=?",
            (pct, iso(), job_id),
        )
        cur = get_job(job_id, user_id=user_id)
        if cur:
            emit_job_event(user_id, "job.progress", job_payload(cur))
            row = db.fetchone("SELECT * FROM plugin_jobs WHERE id = ?", (job_id,))
            if row:
                _sync_job_message(dict(row))
        return cur
    if status == "done":
        if result is None:
            payload: Any = {}
        elif isinstance(result, (dict, list)):
            payload = result
        else:
            payload = {"text": result}
        if isinstance(payload, dict):
            try:
                inp = json.loads(job.get("input_json") or "{}")
                if inp.get("_upload_file_id") and not payload.get("file_id"):
                    payload["file_id"] = inp["_upload_file_id"]
            except Exception:
                pass
        db.execute(
            """
            UPDATE plugin_jobs SET status='done', result_json=?, progress_pct=100, updated_at=? WHERE id=?
            """,
            (json.dumps(payload, ensure_ascii=False), iso(), job_id),
        )
        mark_busy(inst["id"], -1)
        touch_instance(inst["id"])
        cur = get_job(job_id, user_id=user_id)
        if cur:
            emit_job_event(user_id, "job.done", job_payload(cur))
            row = db.fetchone("SELECT * FROM plugin_jobs WHERE id = ?", (job_id,))
            if row:
                _sync_job_message(dict(row), result=payload)
            _log_plugin_usage(user_id=user_id, job=job, status="ok")
            _maybe_chain_video_understand(job=job, result=payload if isinstance(payload, dict) else {})
        return cur
    if status in ("failed", "cancelled"):
        err = str(remote.get("error") or status)
        db.execute(
            """
            UPDATE plugin_jobs SET status=?, error=?, updated_at=? WHERE id=?
            """,
            (status, err, iso(), job_id),
        )
        mark_busy(inst["id"], -1)
        cur = get_job(job_id, user_id=user_id)
        if cur:
            emit_job_event(user_id, f"job.{status}", job_payload(cur))
            row = db.fetchone("SELECT * FROM plugin_jobs WHERE id = ?", (job_id,))
            if row:
                _sync_job_message(dict(row))
            if status == "failed":
                _log_plugin_usage(user_id=user_id, job=job, status="error", error=err)
        return cur
    return get_job(job_id, user_id=user_id)


def cancel_job(job_id: str, *, user_id: str) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT * FROM plugin_jobs WHERE id = ? AND user_id = ?",
        (job_id, user_id),
    )
    if not row:
        return None
    job = dict(row)
    inst = db.fetchone(
        "SELECT * FROM plugin_instances WHERE id = ?", (job.get("instance_id"),)
    )
    if inst and job.get("remote_job_id"):
        inst = dict(inst)
        try:
            with httpx.Client(timeout=10.0) as client:
                client.delete(
                    f"{inst['base_url'].rstrip('/')}/jobs/{job['remote_job_id']}",
                    headers={"Authorization": f"Bearer {inst['token']}"},
                )
        except Exception:
            pass
        mark_busy(inst["id"], -1)
    db.execute(
        "UPDATE plugin_jobs SET status='cancelled', updated_at=? WHERE id=?",
        (iso(), job_id),
    )
    cur = get_job(job_id, user_id=user_id)
    if cur:
        emit_job_event(user_id, "job.cancelled", job_payload(cur))
    return cur


def interrupt_running_jobs_on_startup() -> int:
    """网关重启：进行中任务标为 interrupted。"""
    rows = db.fetchall(
        "SELECT id, instance_id FROM plugin_jobs WHERE status IN ('queued','running')"
    )
    n = 0
    for r in rows:
        db.execute(
            "UPDATE plugin_jobs SET status='interrupted', updated_at=? WHERE id=?",
            (iso(), r["id"]),
        )
        if r["instance_id"]:
            try:
                mark_busy(r["instance_id"], -1)
            except Exception:
                pass
        n += 1
    return n


def _maybe_chain_video_understand(*, job: Dict[str, Any], result: Dict[str, Any]) -> None:
    """下载完成且 input.then_understand 时，网关侧自动开 video.understand（App 杀了也能接力）。"""
    if (job.get("capability") or "") != "media.download":
        return
    try:
        inp = json.loads(job.get("input_json") or "{}")
    except Exception:
        return
    if not isinstance(inp, dict) or not inp.get("then_understand"):
        return
    if (inp.get("action") or "download") == "probe":
        return
    # 防重复：已有 chained 标记则跳过
    if result.get("chained_job_id"):
        return
    url = str(inp.get("url") or result.get("url") or "").strip()
    file_id = str(result.get("file_id") or inp.get("_upload_file_id") or "").strip()
    if not url and not file_id:
        return
    user_id = job["user_id"]
    urow = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (user_id,))
    is_admin = bool(urow["is_admin"]) if urow is not None else False
    chain_input: Dict[str, Any] = {
        "prompt": "简要描述视频内容",
        "source_job_id": job["id"],
    }
    if url:
        chain_input["url"] = url
    if file_id:
        chain_input["file_id"] = file_id
    try:
        child = create_job(
            user_id=user_id,
            is_admin=is_admin,
            capability="video.understand",
            plugin_id="video-mage",
            input_data=chain_input,
            persona_id=job.get("persona_id") or "",
            conversation_id=job.get("conversation_id") or "",
            timeout_seconds=600,
        )
    except Exception as exc:  # noqa: BLE001
        # 接力失败不改写下载结果，只记 error 字段便于排查
        try:
            merged = dict(result)
            merged["chain_error"] = str(exc)
            db.execute(
                "UPDATE plugin_jobs SET result_json=?, updated_at=? WHERE id=?",
                (json.dumps(merged, ensure_ascii=False), iso(), job["id"]),
            )
        except Exception:
            pass
        return
    child_id = (child or {}).get("id") or ""
    if not child_id:
        return
    try:
        merged = dict(result)
        merged["chained_job_id"] = child_id
        db.execute(
            "UPDATE plugin_jobs SET result_json=?, updated_at=? WHERE id=?",
            (json.dumps(merged, ensure_ascii=False), iso(), job["id"]),
        )
    except Exception:
        pass


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    result = {}
    try:
        result = json.loads(job.get("result_json") or "{}")
    except Exception:
        result = {}
    out = {
        "id": job["id"],
        "capability": job["capability"],
        "plugin_id": job["plugin_id"],
        "status": job["status"],
        "progress_pct": job.get("progress_pct") or 0,
        "progress_msg": job.get("progress_msg") or "",
        "result": result,
        "error": job.get("error") or "",
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "conversation_id": job.get("conversation_id") or "",
        "message_id": job.get("message_id") or "",
    }
    # 方便客户端：把常用结果字段摊到顶层
    if isinstance(result, dict):
        for k in ("file_id", "title", "duration", "size", "summary", "text", "chained_job_id"):
            if k in result and k not in out:
                out[k] = result[k]
    return out
