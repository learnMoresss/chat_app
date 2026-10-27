"""按心情 / 聊天上下文从曲库抽歌单。"""
from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional, Sequence

from . import db
from .security import iso, new_id

_MOOD_KEYWORDS: Dict[str, List[str]] = {
    "joy": ["开心", "快乐", "甜", "笑", "阳光", "happy", "sweet", "love", "跳", "蹦", "派对", "party"],
    "sadness": ["难过", "伤心", "哭", "孤独", "想你", "rain", "sad", "lonely", "夜", "分手", "痛"],
    "excitement": ["燃", "嗨", "兴奋", "冲", "rock", "火", "热", "energy", "夜店", "dance"],
    "worry": ["担心", "焦虑", "安静", "慢", "piano", "soft", "轻", "安抚"],
    "annoyance": ["烦", "怼", "硬", "狠", "diss", "狂", "狠"],
    "curiosity": ["探索", "发现", "新", "奇", "dream", "star", "宇宙", "未知"],
    "calm": ["安静", "轻", "眠", "lofi", "ambient", "雨", "海", "风"],
    "romance": ["爱", "情", "吻", "心", "love", "heart", "玫瑰", "暧昧"],
}


def _tokens(text: str) -> List[str]:
    t = (text or "").lower()
    parts = re.findall(r"[\u4e00-\u9fff]{1,8}|[a-z0-9]{2,16}", t)
    return [p for p in parts if p]


def _mood_terms(affect: Optional[dict], hint: str) -> List[str]:
    terms: List[str] = []
    hint_l = (hint or "").strip().lower()
    if hint_l:
        terms.extend(_tokens(hint_l))
        for k, words in _MOOD_KEYWORDS.items():
            if k in hint_l or any(w in hint_l for w in words[:3]):
                terms.extend(words)
    if isinstance(affect, dict):
        scored = sorted(
            ((float(affect.get(k, 35) or 35), k) for k in _MOOD_KEYWORDS if k in ("joy", "sadness", "excitement", "worry", "annoyance", "curiosity")),
            reverse=True,
        )
        for score, key in scored[:2]:
            if score >= 40:
                terms.extend(_MOOD_KEYWORDS.get(key, []))
    if not terms:
        terms.extend(_MOOD_KEYWORDS["calm"] + _MOOD_KEYWORDS["romance"])
    # 去重保序
    seen = set()
    out = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:40]


def curate_playlist(
    *,
    user_id: str,
    name: str,
    affect: Optional[dict] = None,
    hint: str = "",
    context_text: str = "",
    limit: int = 12,
) -> Dict[str, Any]:
    """抽歌写入 music_playlists，返回 playlist + tracks。"""
    terms = _mood_terms(affect, hint)
    terms.extend(_tokens(context_text)[:20])
    rows = db.fetchall(
        """
        SELECT id, title, artist, path, duration_sec, source
        FROM music_tracks WHERE user_id = ?
        ORDER BY updated_at DESC LIMIT 4000
        """,
        (user_id,),
    )
    scored: List[tuple] = []
    for r in rows:
        title = f"{r['title'] or ''} {r['artist'] or ''}".lower()
        score = 0
        for t in terms:
            if t and t.lower() in title:
                score += 3 if len(t) > 1 else 1
        if score <= 0:
            continue
        # 略偏本地 cache / mp3，移动端播放更稳（flac 体积大且部分链路慢）
        if (r["source"] or "") == "cache":
            score += 1
        path_l = (r["path"] or "").lower()
        if path_l.endswith(".mp3"):
            score += 2
        elif path_l.endswith(".m4a") or path_l.endswith(".aac"):
            score += 1
        elif path_l.endswith(".flac"):
            score -= 1
        scored.append((score, dict(r)))
    scored.sort(key=lambda x: (-x[0], random.random()))
    picks = [d for _, d in scored[: max(limit * 3, limit)]]
    random.shuffle(picks)
    picks = picks[:limit]
    if len(picks) < limit and rows:
        # 关键词不够就随机补；优先 mp3
        pool = [dict(r) for r in rows]
        pool.sort(key=lambda r: 0 if (r.get("path") or "").lower().endswith(".mp3") else 1)
        random.shuffle(pool)
        have = {p["id"] for p in picks}
        for r in pool:
            if r["id"] in have:
                continue
            picks.append(r)
            if len(picks) >= limit:
                break
    now = iso()
    pid = new_id("mpl_")
    # kind=session：一起听临时歌单；曲目仍来自账号全局 music_tracks
    db.execute(
        """
        INSERT INTO music_playlists(id, user_id, name, kind, source_url, meta_json, created_at, updated_at)
        VALUES (?,?,?,'session','','{}',?,?)
        """,
        (pid, user_id, name.strip() or "一起听", now, now),
    )
    for i, tr in enumerate(picks):
        db.execute(
            """
            INSERT INTO music_playlist_items(id, playlist_id, track_id, position, created_at)
            VALUES (?,?,?,?,?)
            """,
            (new_id("mpli_"), pid, tr["id"], i, now),
        )
    return {
        "playlist_id": pid,
        "name": name.strip() or "一起听",
        "tracks": picks,
        "terms": terms[:12],
    }
