from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import db
from ..config import get_settings
from ..dsh_gateway import dsh_complete, require_dsh
from ..security import current_user, iso, new_id

router = APIRouter(prefix="/api/v1/extract", tags=["extract"])

# 复用思路（不强制安装重依赖，可插拔）：
# - 聊天习惯：OpenSelf / clone-chat / gpt-chat-analysis → LLM 抽风格画像
# - 声音特征：Resemblyzer / OpenVoice / Coqui speaker encoder → embedding 文件
# 本阶段：习惯走 LLM；声音先存参考音频 + 基础声学摘要，embedding 标记 pending_resemblyzer


HABIT_PROMPT = """你是聊天风格分析器（思路对齐 OpenSelf / clone-chat）。
根据上传的聊天文本，提取「说话人」的习惯画像，只输出 JSON：
{{
  "catchphrases": ["口头禅1"],
  "tone": "语气一句话",
  "emoji_habits": "表情习惯",
  "reply_length": "偏短/中/长",
  "punctuation": "标点习惯",
  "addressing": "怎么称呼对方",
  "topics": ["常聊话题"],
  "sample_lines": ["典型原话1", "典型原话2"],
  "summary": "80字内风格摘要，可直接塞进人设 bio 补充"
}}
不要 Markdown。文本如下：
---
{text}
---
"""


class HabitResult(BaseModel):
    persona_id: str
    profile: Dict[str, Any]
    source: str = "llm"


class VoiceProfileOut(BaseModel):
    id: str
    persona_id: str
    filename: str
    duration_sec: float
    sample_rate: int
    channels: int
    embed_status: str
    embed_note: str
    created_at: str


def _owned_persona(user_id: str, persona_id: str):
    row = db.fetchone(
        "SELECT * FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="人设不存在")
    return row


def _upload_dir(user_id: str) -> Path:
    root = get_settings().data_dir / "extract" / user_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _basic_wav_stats(path: Path) -> Dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            ch = w.getnchannels()
            dur = frames / float(rate) if rate else 0.0
            return {"duration_sec": round(dur, 2), "sample_rate": rate, "channels": ch}
    except Exception:
        # 非 wav：只记文件大小
        size = path.stat().st_size if path.exists() else 0
        return {"duration_sec": 0.0, "sample_rate": 0, "channels": 0, "bytes": size}


@router.post("/habits", response_model=HabitResult)
async def extract_habits(
    persona_id: str = Form(...),
    text: str = Form(""),
    file: Optional[UploadFile] = File(None),
    apply_to_bio: str = Form("false"),
    user: Dict[str, Any] = Depends(current_user),
) -> HabitResult:
    """从聊天文本提取说话习惯，可选写回人设。"""
    _owned_persona(user["id"], persona_id)
    body = (text or "").strip()
    if file is not None and file.filename:
        raw = await file.read()
        try:
            body = (body + "\n" + raw.decode("utf-8", errors="ignore")).strip()
        except Exception:
            body = body or raw.decode("utf-8", errors="replace")
    if len(body) < 20:
        raise HTTPException(status_code=400, detail="请提供足够的聊天文本（或上传 .txt）")

    system = "只输出 JSON 对象。"
    user_prompt = HABIT_PROMPT.format(text=body[:12000])
    try:
        require_dsh()
        out = await dsh_complete(
            user_id=user["id"],
            conversation_id=f"sys-extract-habit-{persona_id}-{new_id('')}",
            text=user_prompt,
            system=system,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"模型提取失败：{exc}") from exc

    cleaned = out.strip()
    if "```" in cleaned:
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        profile = json.loads(cleaned.strip())
        if not isinstance(profile, dict):
            raise ValueError("not object")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="模型未返回合法 JSON") from exc

    eid = new_id("ex_")
    now = iso()
    db.execute(
        """
        INSERT INTO extract_jobs(id, user_id, persona_id, kind, result_json, created_at)
        VALUES (?, ?, ?, 'habits', ?, ?)
        """,
        (eid, user["id"], persona_id, json.dumps(profile, ensure_ascii=False), now),
    )

    if str(apply_to_bio).lower() in ("1", "true", "yes", "on"):
        summary = str(profile.get("summary") or "")
        if summary:
            row = db.fetchone("SELECT bio FROM personas WHERE id = ?", (persona_id,))
            bio = (row["bio"] if row else "") or ""
            marker = "\n\n【提取的说话习惯】\n"
            if "【提取的说话习惯】" in bio:
                bio = bio.split("【提取的说话习惯】")[0].rstrip()
            bio = f"{bio}{marker}{summary}".strip()
            db.execute("UPDATE personas SET bio = ? WHERE id = ? AND user_id = ?", (bio, persona_id, user["id"]))

    return HabitResult(persona_id=persona_id, profile=profile)


@router.post("/voice", response_model=VoiceProfileOut)
async def extract_voice(
    persona_id: str = Form(...),
    file: UploadFile = File(...),
    user: Dict[str, Any] = Depends(current_user),
) -> VoiceProfileOut:
    """上传参考语音，保存文件并写基础声学信息。
    后续可接 Resemblyzer / OpenVoice 生成 speaker embedding。
    """
    _owned_persona(user["id"], persona_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="请上传音频文件")
    suffix = Path(file.filename).suffix.lower() or ".wav"
    if suffix not in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"):
        raise HTTPException(status_code=400, detail="支持 wav/mp3/m4a/ogg/flac/webm")

    vid = new_id("vp_")
    dest = _upload_dir(user["id"]) / f"{vid}{suffix}"
    data = await file.read()
    if len(data) < 1000:
        raise HTTPException(status_code=400, detail="音频太短或为空")
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="音频请小于 30MB")
    dest.write_bytes(data)
    stats = _basic_wav_stats(dest) if suffix == ".wav" else _basic_wav_stats(dest)

    embed_status = "pending"
    embed_note = "已存参考音频。接 Resemblyzer/OpenVoice 后可生成 speaker embedding，供 TTS/通话复用。"
    # 可选：若环境装了 resemblyzer 则尝试
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
        import numpy as np  # type: ignore

        wav = preprocess_wav(str(dest))
        enc = VoiceEncoder()
        embed = enc.embed_utterance(wav)
        np.save(str(dest.with_suffix(".npy")), embed)
        embed_status = "ready"
        embed_note = "已用 Resemblyzer 生成 256 维 voice embedding（.npy）。"
    except Exception:
        pass

    now = iso()
    db.execute(
        """
        INSERT INTO voice_profiles(
          id, user_id, persona_id, filename, path, duration_sec, sample_rate, channels,
          embed_status, embed_note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            vid,
            user["id"],
            persona_id,
            file.filename,
            str(dest),
            float(stats.get("duration_sec") or 0),
            int(stats.get("sample_rate") or 0),
            int(stats.get("channels") or 0),
            embed_status,
            embed_note,
            now,
        ),
    )
    return VoiceProfileOut(
        id=vid,
        persona_id=persona_id,
        filename=file.filename,
        duration_sec=float(stats.get("duration_sec") or 0),
        sample_rate=int(stats.get("sample_rate") or 0),
        channels=int(stats.get("channels") or 0),
        embed_status=embed_status,
        embed_note=embed_note,
        created_at=now,
    )


@router.get("/voice/{persona_id}", response_model=List[VoiceProfileOut])
def list_voice_profiles(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[VoiceProfileOut]:
    _owned_persona(user["id"], persona_id)
    rows = db.fetchall(
        """
        SELECT * FROM voice_profiles
        WHERE user_id = ? AND persona_id = ?
        ORDER BY created_at DESC
        """,
        (user["id"], persona_id),
    )
    return [
        VoiceProfileOut(
            id=r["id"],
            persona_id=r["persona_id"],
            filename=r["filename"],
            duration_sec=float(r["duration_sec"] or 0),
            sample_rate=int(r["sample_rate"] or 0),
            channels=int(r["channels"] or 0),
            embed_status=r["embed_status"],
            embed_note=r["embed_note"] or "",
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.get("/habits/{persona_id}")
def list_habit_jobs(persona_id: str, user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    _owned_persona(user["id"], persona_id)
    rows = db.fetchall(
        """
        SELECT id, result_json, created_at FROM extract_jobs
        WHERE user_id = ? AND persona_id = ? AND kind = 'habits'
        ORDER BY created_at DESC LIMIT 20
        """,
        (user["id"], persona_id),
    )
    out = []
    for r in rows:
        try:
            profile = json.loads(r["result_json"])
        except Exception:
            profile = {}
        out.append({"id": r["id"], "profile": profile, "created_at": r["created_at"]})
    return out
