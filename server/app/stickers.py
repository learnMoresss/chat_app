"""表情包：账号级全局库（类似曲库）；入库默认 OCR 提炼图上文字作描述，按描述匹配取用。

owner_kind=library 为账号共享池；历史 persona/user 行仍计入同一账号池检索。
不默认把用户聊天图镜像进库（需显式 collect）。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import db
from .security import iso, new_id

LIBRARY_KIND = "library"


def _file_hash(user_id: str, file_id: str) -> str:
    row = db.fetchone(
        "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
        (file_id, user_id),
    )
    if row is None:
        row = db.fetchone("SELECT path FROM media_files WHERE id = ?", (file_id,))
    if row is None:
        return ""
    p = Path(row["path"] or "")
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:32]


def _media_path(user_id: str, file_id: str) -> Optional[Path]:
    row = db.fetchone(
        "SELECT path FROM media_files WHERE id = ? AND user_id = ?",
        (file_id, user_id),
    )
    if row is None:
        row = db.fetchone("SELECT path FROM media_files WHERE id = ?", (file_id,))
    if row is None:
        return None
    p = Path(str(row["path"] or ""))
    return p if p.is_file() else None


def _refine_ocr_label(texts: List[str]) -> str:
    """把 OCR 多行压成一句可检索描述（优先图上短句）。"""
    cleaned: List[str] = []
    for raw in texts:
        t = re.sub(r"\s+", "", (raw or "").strip())
        if not t:
            continue
        if re.fullmatch(r"[\W\d_]+", t, flags=re.UNICODE):
            continue
        if len(t) > 40:
            t = t[:40]
        cleaned.append(t)
    if not cleaned:
        return ""
    # 表情包常见：一行梗；多行则主句 + 辅句
    short = [c for c in cleaned if len(c) <= 18]
    pool = short or cleaned
    primary = max(pool, key=len)
    extras = [c for c in pool if c != primary][:2]
    label = primary if not extras else f"{primary} / {' / '.join(extras)}"
    return label[:64]


def _tags_json(tags: List[str]) -> str:
    cleaned: List[str] = []
    for t in tags:
        s = re.sub(r"\s+", "", (t or "").strip())
        if not s or s in cleaned:
            continue
        cleaned.append(s[:24])
        if len(cleaned) >= 8:
            break
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else ""


def _parse_tags(raw: str) -> List[str]:
    t = (raw or "").strip()
    if not t:
        return []
    if t.startswith("["):
        try:
            arr = json.loads(t)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()][:8]
        except Exception:
            pass
    return [x.strip() for x in re.split(r"[,，、|/]", t) if x.strip()][:8]


def annotate_sticker_image(*, user_id: str, file_id: str) -> Dict[str, str]:
    """语义标注：优先本地 VL label/tags；OCR 有字时并入 tags。"""
    path = _media_path(user_id, file_id)
    if path is None:
        return {"label": "", "emotion": "", "ocr": "", "tags": ""}
    vl_label = ""
    try:
        from .vision_gen import caption_sticker_path

        vl_label = (caption_sticker_path(path) or "").strip()
    except Exception:
        vl_label = ""
    try:
        from .vision_gen import ocr_image_path

        _summary, lines = ocr_image_path(path)
    except Exception:
        lines = []
    texts = [str(x.get("text") or "").strip() for x in (lines or []) if (x.get("text") or "").strip()]
    # 过滤水印站名
    texts = [
        t
        for t in texts
        if t
        and "nipic" not in t.lower()
        and "www." not in t.lower()
        and not re.search(r"ID:\d+", t, flags=re.I)
    ]
    ocr_join = "；".join(texts[:16])
    ocr_label = _refine_ocr_label(texts)
    label = ""
    if vl_label:
        # 去掉模板腔「主体：…动作：…」
        cleaned = re.sub(r"[主体动作情绪场景背景][：:]\s*", "", vl_label)
        cleaned = re.sub(r"\s+", "", cleaned)
        cleaned = cleaned.strip("「」【】\"'“”‘’《》[]()（）")
        cleaned = re.sub(r"[。！？.!,，、]+$", "", cleaned)
        # 截到第一句短段
        for sep in ("。", "！", "?", "，", ",", "；"):
            if sep in cleaned:
                cleaned = cleaned.split(sep)[0]
                break
        if len(cleaned) > 16:
            cleaned = cleaned[:16]
        # 重复字压缩（眼睛眼睛眼睛…）
        cleaned = re.sub(r"(.)\1{2,}", r"\1\1", cleaned)
        label = cleaned[:64] if cleaned else vl_label[:16]
    elif ocr_label:
        label = ocr_label
    if not label:
        label = "未识别表情"
    tags: List[str] = []
    if label and label not in ("未识别表情", "无字表情"):
        tags.append(label[:24])
    if ocr_label and ocr_label not in tags:
        tags.append(ocr_label[:24])
    for t in texts[:4]:
        tt = re.sub(r"\s+", "", t)[:16]
        if tt and tt not in tags and "www" not in tt.lower():
            tags.append(tt)
    emotion = ""
    for key in ("翻白眼", "无语", "嫌弃", "委屈", "开心", "好的", "加油", "晚安", "早安", "哈哈哈", "哭"):
        if key in label or key in ocr_join:
            emotion = key[:32]
            break
    return {
        "label": label[:64],
        "emotion": emotion,
        "ocr": ocr_join[:200],
        "tags": _tags_json(tags),
    }


def reannotate_sticker(*, user_id: str, sticker_id: str, overwrite: bool = True) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT * FROM stickers WHERE id = ? AND user_id = ?",
        (sticker_id, user_id),
    )
    if row is None:
        return None
    if not overwrite and (row["label"] or "").strip() and (row["label"] or "") not in (
        "无字表情",
        "未识别表情",
    ):
        return _out(row)
    ann = annotate_sticker_image(user_id=user_id, file_id=row["file_id"])
    db.execute(
        "UPDATE stickers SET label = ?, emotion = ?, tags = ? WHERE id = ?",
        (ann["label"], ann["emotion"], ann.get("tags") or "", sticker_id),
    )
    r2 = db.fetchone("SELECT * FROM stickers WHERE id = ?", (sticker_id,))
    return {**_out(r2), "ocr": ann.get("ocr", "")} if r2 else None

def reannotate_library(*, user_id: str, limit: int = 200) -> Dict[str, int]:
    rows = db.fetchall(
        """
        SELECT id FROM stickers WHERE user_id = ?
        ORDER BY created_at DESC LIMIT ?
        """,
        (user_id, max(1, min(int(limit), 500))),
    )
    ok = 0
    for r in rows:
        if reannotate_sticker(user_id=user_id, sticker_id=r["id"], overwrite=True):
            ok += 1
    return {"updated": ok, "total": len(rows)}


# 出站启发式：正文命中图上常见字/梗时补表情意图（优先用描述词，不是空泛情绪）
_CONTENT_HINTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("翻白眼", ("翻白眼", "无语", "服了", "无语了", "又来")),
    ("好的", ("好的", "好哒", "嗯嗯", "收到")),
    ("哈哈哈", ("哈哈", "嘿嘿", "笑死", "太好笑")),
    ("哭", ("想哭", "哭了", "呜呜", "难过")),
    ("加油", ("加油", "冲", "元气")),
    ("晚安", ("晚安", "睡觉")),
    ("早安", ("早安", "早呀")),
)


def suggest_sticker_query(text: str) -> Optional[str]:
    """从回复正文猜一个检索词（对应 OCR 描述），没有则 None。"""
    t = (text or "").strip()
    if not t or len(t) < 2:
        return None
    for label, keys in _CONTENT_HINTS:
        if any(k in t for k in keys):
            return label
    return None


# 兼容旧名
suggest_sticker_emotion = suggest_sticker_query
SEED_EMOTIONS = ("好的", "哈哈哈", "无语", "加油", "晚安")


def sticker_freq_params(freq: str) -> tuple[float, int]:
    """表情触发档位 → (probability, cooldown_seconds)。默认高。"""
    f = (freq or "high").strip().lower()
    if f in ("low", "低"):
        return 0.22, 120
    if f in ("medium", "mid", "中", "中等"):
        return 0.48, 55
    # high / 高
    return 0.82, 22


def user_asks_sticker_pack(text: str) -> bool:
    """对方是否明确要一组/收集表情（才允许发组图）。"""
    t = (text or "").strip()
    if not t:
        return False
    keys = (
        "一组表情",
        "表情组",
        "表情包一组",
        "发一组表情",
        "来一组表情",
        "推荐一组表情",
        "收集表情",
        "收藏表情",
        "整组表情",
        "表情合集",
        "贴纸组",
        "一组贴纸",
        "打包表情",
    )
    if any(k in t for k in keys):
        return True
    if re.search(r"(一组|整组|一包|合集).{0,6}(表情|贴纸)", t):
        return True
    if re.search(r"(收集|收藏|推荐).{0,4}(一组)?(表情|贴纸)", t):
        return True
    return False


def demote_sticker_packs_unless_asked(intents: list, *, user_text: str = "") -> list:
    """未明确要组图时，把 sticker_pack 降为单张 sticker。"""
    allow_pack = user_asks_sticker_pack(user_text)
    out = []
    for i in intents or []:
        kind = str(i.get("kind") or "")
        if kind == "sticker_pack" and not allow_pack:
            item = dict(i)
            item["kind"] = "sticker"
            out.append(item)
        else:
            out.append(i)
    return out


def maybe_augment_sticker_intents(
    text: str,
    intents: list,
    *,
    cooled: bool = False,
    probability: float = 0.55,
    extra_text: str = "",
    user_id: str = "",
) -> list:
    """模型没写表情标记时，按正文里的词/梗补一条单张 sticker（不发组图）。"""
    out = list(intents or [])
    if cooled or any(i.get("kind") in ("sticker", "sticker_pack") for i in out):
        return out
    q = suggest_sticker_query(text) or suggest_sticker_query(extra_text)
    # 若库里有与正文重叠的描述，优先用那条 label
    if user_id:
        blob = f"{text} {extra_text}"
        try:
            rows = db.fetchall(
                """
                SELECT label FROM stickers
                WHERE user_id = ? AND IFNULL(label,'') != '' AND label != '无字表情'
                ORDER BY created_at DESC LIMIT 40
                """,
                (user_id,),
            )
            for r in rows:
                lab = (r["label"] or "").strip()
                head = lab.split("/")[0].strip()
                if head and len(head) >= 2 and head in blob:
                    q = head
                    break
        except Exception:
            pass
    if not q:
        return out
    if random.random() > probability:
        return out
    # 日常只补单张；组图需对方明确要收集
    out.append({"kind": "sticker", "prompt": q})
    return out


def add_sticker(
    *,
    user_id: str,
    file_id: str,
    label: str = "",
    emotion: str = "",
    owner_kind: str = LIBRARY_KIND,
    owner_id: str = "",
    pack_id: str = "",
    skip_ocr: bool = False,
) -> Dict[str, Any]:
    owned = db.fetchone(
        "SELECT id FROM media_files WHERE id = ? AND user_id = ?",
        (file_id, user_id),
    )
    if owned is None:
        # 全局组图可能在别人名下
        owned = db.fetchone("SELECT id FROM media_files WHERE id = ?", (file_id,))
    if owned is None:
        raise ValueError("图片不存在")
    # 默认账号全局库；兼容旧 user/persona 写入
    if owner_kind not in ("user", "persona", LIBRARY_KIND):
        owner_kind = LIBRARY_KIND
    oid = owner_id or user_id
    if owner_kind == LIBRARY_KIND:
        oid = user_id
    pid = (pack_id or "").strip()
    fhash = _file_hash(user_id, file_id)
    # 去重：同 pack 内同 hash；无 pack 则账号级
    if fhash:
        if pid:
            exist_h = db.fetchone(
                """
                SELECT * FROM stickers
                WHERE pack_id = ? AND file_hash = ?
                """,
                (pid, fhash),
            )
        else:
            exist_h = db.fetchone(
                """
                SELECT * FROM stickers
                WHERE user_id = ? AND file_hash = ?
                """,
                (user_id, fhash),
            )
        if exist_h is not None:
            return {**_out(exist_h), "deduped": True}
    if pid:
        exist = db.fetchone(
            """
            SELECT * FROM stickers
            WHERE pack_id = ? AND file_id = ?
            """,
            (pid, file_id),
        )
    else:
        exist = db.fetchone(
            """
            SELECT * FROM stickers
            WHERE user_id = ? AND file_id = ?
            """,
            (user_id, file_id),
        )
    if exist:
        return {**_out(exist), "deduped": True}

    # 氛围示意卡禁止进表情库
    if _is_mood_placeholder(file_id, user_id):
        raise ValueError("示意氛围卡不能当表情包入库")

    lab = (label or "").strip()
    emo = (emotion or "").strip()
    tags_s = ""
    # 默认 VL+OCR：空标签或含糊情绪时重标
    vague = {"", "开心", "委屈", "嫌弃", "元气", "翻白眼", "高兴", "难过", "无字表情", "未识别表情"}
    if not skip_ocr and (not lab or lab in vague or (emo in vague and not lab)):
        ann = annotate_sticker_image(user_id=user_id, file_id=file_id)
        if ann.get("label"):
            lab = ann["label"]
        if not emo and ann.get("emotion"):
            emo = ann["emotion"]
        tags_s = ann.get("tags") or ""
    if not lab:
        lab = emo or "未识别表情"

    sid = new_id("stk_")
    now = iso()
    db.execute(
        """
        INSERT INTO stickers(id, user_id, owner_kind, owner_id, file_id, label, emotion, created_at, file_hash, pack_id, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sid,
            user_id,
            owner_kind,
            oid,
            file_id,
            lab[:64],
            emo[:32],
            now,
            fhash,
            pid,
            tags_s[:512],
        ),
    )
    row = db.fetchone("SELECT * FROM stickers WHERE id = ?", (sid,))
    assert row is not None
    return {**_out(row), "deduped": False}


def list_stickers(
    *,
    user_id: str,
    owner_kind: str = "",
    owner_id: str = "",
    emotion: str = "",
    pack_id: str = "",
    q: str = "",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    sql = "SELECT * FROM stickers WHERE user_id = ?"
    args: list = [user_id]
    if owner_kind:
        sql += " AND owner_kind = ?"
        args.append(owner_kind)
    if owner_id:
        sql += " AND owner_id = ?"
        args.append(owner_id)
    if pack_id:
        sql += " AND pack_id = ?"
        args.append(pack_id.strip())
    query = (q or emotion or "").strip()
    if query:
        sql += " AND (label LIKE ? OR emotion LIKE ? OR label = ? OR emotion = ?)"
        like = f"%{query}%"
        args.extend([like, like, query, query])
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [_out(r) for r in db.fetchall(sql, tuple(args))]


def delete_sticker(*, user_id: str, sticker_id: str) -> bool:
    """删表情：自己收藏的，或落在自己账号组里的条目。"""
    row = db.fetchone(
        """
        SELECT s.id FROM stickers s
        LEFT JOIN sticker_packs p ON p.id = s.pack_id
        WHERE s.id = ?
          AND (
            s.user_id = ?
            OR (p.scope = 'account' AND p.owner_user_id = ?)
          )
        """,
        (sticker_id, user_id, user_id),
    )
    if row is None:
        return False
    db.execute("DELETE FROM stickers WHERE id = ?", (sticker_id,))
    return True


def library_sticker_count(*, user_id: str) -> int:
    row = db.fetchone(
        "SELECT COUNT(*) AS n FROM stickers WHERE user_id = ?",
        (user_id,),
    )
    return int(row["n"] if row else 0)


def persona_sticker_count(*, user_id: str, persona_id: str) -> int:
    """兼容旧调用：现为账号全局库计数。"""
    _ = persona_id
    return library_sticker_count(user_id=user_id)


def library_sticker_summary(*, user_id: str, limit: int = 12) -> List[Dict[str, Any]]:
    """OCR 描述 → 数量（供 DSH 提示 / 管理台）；兼容旧字段 emotion。"""
    rows = db.fetchall(
        """
        SELECT
          CASE
            WHEN IFNULL(label,'') != '' AND label != '无字表情' THEN label
            WHEN IFNULL(emotion,'') != '' THEN emotion
            ELSE '未标注'
          END AS tag,
          COUNT(*) AS n
        FROM stickers
        WHERE user_id = ?
        GROUP BY tag
        ORDER BY n DESC
        LIMIT ?
        """,
        (user_id, max(1, min(int(limit), 40))),
    )
    out = []
    for r in rows:
        tag = (r["tag"] or "").strip() or "未标注"
        out.append({"label": tag, "emotion": tag, "count": int(r["n"])})
    return out


def persona_sticker_summary(*, user_id: str, persona_id: str = "", limit: int = 12) -> List[Dict[str, Any]]:
    _ = persona_id
    return library_sticker_summary(user_id=user_id, limit=limit)


def ensure_persona_stickers(
    *,
    user_id: str,
    persona_id: str = "",
    persona_name: str = "",
    min_count: int = 3,
) -> int:
    """账号全局库不足时尝试真生图冷启动；不再用氛围示意卡冒充表情。"""
    _ = persona_id
    _ = persona_name
    have = library_sticker_count(user_id=user_id)
    if have >= min_count:
        return have
    # 清掉历史示意卡，避免「开心」反复发错图
    try:
        rows = db.fetchall(
            "SELECT id, file_id FROM stickers WHERE user_id = ? LIMIT 80",
            (user_id,),
        )
        for r in rows:
            if _is_mood_placeholder(r["file_id"], user_id):
                db.execute("DELETE FROM stickers WHERE id = ?", (r["id"],))
    except Exception:
        pass
    return library_sticker_count(user_id=user_id)


def _is_mood_placeholder(file_id: str, user_id: str) -> bool:
    """氛围示意卡不能当表情发出去（绘梨衣「开心」错图根因）。"""
    row = db.fetchone(
        "SELECT source, path FROM media_files WHERE id = ? AND user_id = ?",
        (file_id, user_id),
    )
    if row is None:
        return False
    src = (row["source"] or "").strip().lower()
    if src in ("persona", "mood", "mood_card"):
        # make_mood_card 写入 source=persona，且文件很小/示意
        try:
            path = row["path"] or ""
            import os

            if path and os.path.exists(path) and os.path.getsize(path) < 80_000:
                return True
        except Exception:
            return True
        return src == "mood" or src == "mood_card"
    return False


def pick_by_emotion(
    *,
    user_id: str,
    persona_id: str = "",
    emotion: str,
) -> Optional[Dict[str, Any]]:
    """按描述检索（OCR label 优先，emotion 兜底）。跳过示意氛围卡。"""
    _ = persona_id
    tag = (emotion or "").strip()
    if not tag:
        return None

    def _pick_good(rows) -> Optional[Dict[str, Any]]:
        good = []
        for r in rows:
            owner = r["user_id"]
            if _is_mood_placeholder(r["file_id"], owner):
                continue
            good.append(r)
        if not good:
            return None
        return _out(random.choice(list(good)))

    # 1) label / emotion 精确
    rows = db.fetchall(
        """
        SELECT s.* FROM stickers s
        LEFT JOIN sticker_packs p ON p.id = s.pack_id
        WHERE (s.label = ? OR s.emotion = ?)
          AND (
            s.user_id = ?
            OR p.scope = 'global'
          )
        ORDER BY
          CASE WHEN s.label = ? THEN 0 ELSE 1 END,
          s.created_at DESC
        LIMIT 24
        """,
        (tag, tag, user_id, tag),
    )
    hit = _pick_good(rows) if rows else None
    if hit:
        return hit
    # 2) label 含检索词（OCR 描述）
    like = f"%{tag}%"
    rows = db.fetchall(
        """
        SELECT s.* FROM stickers s
        LEFT JOIN sticker_packs p ON p.id = s.pack_id
        WHERE (
            s.user_id = ?
            OR p.scope = 'global'
          )
          AND (
            s.label LIKE ?
            OR s.emotion LIKE ?
            OR IFNULL(p.tags,'') LIKE ?
            OR IFNULL(p.title,'') LIKE ?
          )
        ORDER BY
          CASE WHEN s.label LIKE ? THEN 0 ELSE 1 END,
          s.created_at DESC
        LIMIT 24
        """,
        (user_id, like, like, like, like, like),
    )
    return _pick_good(rows) if rows else None


def update_sticker(
    *,
    user_id: str,
    sticker_id: str,
    label: Optional[str] = None,
    emotion: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    row = db.fetchone(
        "SELECT * FROM stickers WHERE id = ? AND user_id = ?",
        (sticker_id, user_id),
    )
    if row is None:
        return None
    new_label = row["label"] if label is None else (label or "")[:64]
    new_emo = row["emotion"] if emotion is None else (emotion or "")[:32]
    db.execute(
        "UPDATE stickers SET label = ?, emotion = ? WHERE id = ?",
        (new_label, new_emo, sticker_id),
    )
    r2 = db.fetchone("SELECT * FROM stickers WHERE id = ?", (sticker_id,))
    return _out(r2) if r2 else None


def recent_persona_sticker_secs(
    *,
    user_id: str,
    conv_id: str,
    within_seconds: int = 90,
) -> bool:
    """会话里人设是否刚发过图片气泡（防连发表情）。"""
    row = db.fetchone(
        """
        SELECT created_at FROM messages
        WHERE user_id = ? AND conv_id = ? AND mine = 0 AND kind = 'image'
        ORDER BY seq DESC LIMIT 1
        """,
        (user_id, conv_id),
    )
    if row is None:
        return False
    try:
        from datetime import datetime, timezone

        raw = row["created_at"] or ""
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age < within_seconds
    except Exception:
        return False


def _out(row) -> Dict[str, Any]:
    keys = row.keys() if hasattr(row, "keys") else []
    pack_id = ""
    try:
        pack_id = row["pack_id"] or ""
    except Exception:
        pack_id = ""
    if not pack_id and "pack_id" in keys:
        pack_id = row["pack_id"] or ""
    tags_raw = ""
    try:
        tags_raw = row["tags"] or ""
    except Exception:
        tags_raw = ""
    return {
        "id": row["id"],
        "owner_kind": row["owner_kind"],
        "owner_id": row["owner_id"],
        "file_id": row["file_id"],
        "label": row["label"] or "",
        "emotion": row["emotion"] or "",
        "tags": _parse_tags(tags_raw),
        "pack_id": pack_id or "",
        "created_at": row["created_at"],
    }


def _pack_out(row, *, item_count: int = 0, cover_file_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "owner_user_id": row["owner_user_id"] or "",
        "title": row["title"] or "",
        "tags": row["tags"] or "",
        "source": row["source"] or "user",
        "created_at": row["created_at"],
        "item_count": int(item_count),
        "cover_file_ids": list(cover_file_ids or []),
    }


def create_pack(
    *,
    scope: str,
    owner_user_id: str,
    title: str,
    tags: str = "",
    source: str = "user",
) -> Dict[str, Any]:
    sc = (scope or "").strip().lower()
    if sc not in ("global", "account"):
        raise ValueError("scope 须为 global 或 account")
    src = (source or "user").strip().lower()
    if src not in ("ai", "user", "web"):
        src = "user"
    pid = new_id("spk_")
    now = iso()
    db.execute(
        """
        INSERT INTO sticker_packs(id, scope, owner_user_id, title, tags, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pid,
            sc,
            owner_user_id if sc == "account" else (owner_user_id or ""),
            (title or "未命名组")[:64],
            (tags or "")[:256],
            src,
            now,
        ),
    )
    row = db.fetchone("SELECT * FROM sticker_packs WHERE id = ?", (pid,))
    assert row is not None
    return _pack_out(row)


def get_pack(pack_id: str) -> Optional[Dict[str, Any]]:
    row = db.fetchone("SELECT * FROM sticker_packs WHERE id = ?", (pack_id,))
    if row is None:
        return None
    return _enrich_pack(row)


def _enrich_pack(row) -> Dict[str, Any]:
    pid = row["id"]
    items = db.fetchall(
        """
        SELECT file_id FROM stickers WHERE pack_id = ?
        ORDER BY created_at DESC LIMIT 9
        """,
        (pid,),
    )
    cnt_row = db.fetchone(
        "SELECT COUNT(*) AS n FROM stickers WHERE pack_id = ?",
        (pid,),
    )
    return _pack_out(
        row,
        item_count=int(cnt_row["n"] if cnt_row else 0),
        cover_file_ids=[r["file_id"] for r in items if r["file_id"]],
    )


def list_packs(
    *,
    user_id: str,
    scope: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """列出表情组。

    - account：仅自己的账号组
    - global：仅已订阅的全局组（AI 刚生成的不会自动塞进「全」）
    - 空：账号组 + 已订阅全局组
    """
    limit = max(1, min(int(limit), 100))
    sc = (scope or "").strip().lower()
    if sc == "global":
        rows = db.fetchall(
            """
            SELECT p.* FROM sticker_packs p
            INNER JOIN user_pack_subscriptions s
              ON s.pack_id = p.id AND s.user_id = ?
            WHERE p.scope = 'global'
            ORDER BY s.pinned DESC, p.created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
    elif sc == "account":
        rows = db.fetchall(
            """
            SELECT * FROM sticker_packs
            WHERE scope = 'account' AND owner_user_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, limit),
        )
    else:
        rows = db.fetchall(
            """
            SELECT p.* FROM sticker_packs p
            WHERE (p.scope = 'account' AND p.owner_user_id = ?)
               OR (
                    p.scope = 'global'
                    AND EXISTS (
                      SELECT 1 FROM user_pack_subscriptions s
                      WHERE s.pack_id = p.id AND s.user_id = ?
                    )
                  )
            ORDER BY p.created_at DESC
            LIMIT ?
            """,
            (user_id, user_id, limit),
        )
    return [_enrich_pack(r) for r in rows]


def list_pack_items(*, pack_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    rows = db.fetchall(
        """
        SELECT * FROM stickers WHERE pack_id = ?
        ORDER BY created_at DESC LIMIT ?
        """,
        (pack_id, limit),
    )
    return [_out(r) for r in rows]


def can_access_pack(*, pack: Dict[str, Any], user_id: str, is_service: bool = False) -> bool:
    if pack.get("scope") == "global":
        return True
    if is_service:
        return True
    return (pack.get("owner_user_id") or "") == user_id


def can_append_pack(*, pack: Dict[str, Any], user_id: str, is_service: bool = False) -> bool:
    """账号组仅主人；全局组仅创建者或服务令牌（避免别人/AI 乱塞进未订阅组）。"""
    if is_service:
        return True
    owner = (pack.get("owner_user_id") or "").strip()
    return bool(owner) and owner == user_id


def ensure_account_default_pack(*, user_id: str) -> Dict[str, Any]:
    """旧 collect 无 pack 时写入账号默认组。"""
    row = db.fetchone(
        """
        SELECT * FROM sticker_packs
        WHERE scope = 'account' AND owner_user_id = ? AND title = '我的表情'
        ORDER BY created_at ASC LIMIT 1
        """,
        (user_id,),
    )
    if row is not None:
        return _enrich_pack(row)
    return create_pack(
        scope="account",
        owner_user_id=user_id,
        title="我的表情",
        tags="默认",
        source="user",
    )


def suggest_stickers(*, user_id: str, q: str, limit: int = 12) -> List[Dict[str, Any]]:
    """输入联想：优先 OCR 描述 label，其次 emotion / 组标题。"""
    query = (q or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit), 24))
    like = f"%{query}%"
    rows = db.fetchall(
        """
        SELECT s.* FROM stickers s
        LEFT JOIN sticker_packs p ON p.id = s.pack_id
        WHERE (
            p.scope = 'global'
            OR (p.scope = 'account' AND p.owner_user_id = ?)
            OR (IFNULL(s.pack_id,'') = '' AND s.user_id = ?)
        )
        AND (
            s.label LIKE ? OR s.emotion LIKE ? OR IFNULL(s.tags,'') LIKE ?
            OR IFNULL(p.tags,'') LIKE ? OR IFNULL(p.title,'') LIKE ?
        )
        ORDER BY
          CASE WHEN s.label = ? THEN 0 WHEN s.label LIKE ? THEN 1 ELSE 2 END,
          s.created_at DESC
        LIMIT ?
        """,
        (user_id, user_id, like, like, like, like, like, query, like, limit),
    )
    return [_out(r) for r in rows]


def fork_pack(*, user_id: str, pack_id: str, title: str = "") -> Dict[str, Any]:
    """把 global/他人可见组复制成账号组，并订阅。"""
    src = get_pack(pack_id)
    if src is None:
        raise ValueError("组不存在")
    if not can_access_pack(pack=src, user_id=user_id):
        raise ValueError("无权复制该组")
    new_title = (title or "").strip() or f"{src.get('title') or '表情包'}（我的）"
    new_pack = create_pack(
        scope="account",
        owner_user_id=user_id,
        title=new_title[:64],
        tags=src.get("tags") or "",
        source="user",
    )
    items = list_pack_items(pack_id=pack_id, limit=200)
    added = 0
    for it in items:
        try:
            tags_list = it.get("tags") or []
            tags_s = _tags_json(tags_list) if isinstance(tags_list, list) else ""
            row = add_sticker(
                user_id=user_id,
                file_id=str(it.get("file_id") or ""),
                label=str(it.get("label") or ""),
                emotion=str(it.get("emotion") or ""),
                owner_kind=LIBRARY_KIND,
                owner_id=user_id,
                pack_id=new_pack["id"],
                skip_ocr=True,
            )
            if tags_s and not row.get("deduped"):
                db.execute(
                    "UPDATE stickers SET tags = ? WHERE id = ?",
                    (tags_s[:512], row["id"]),
                )
            if not row.get("deduped"):
                added += 1
        except Exception:
            continue
    subscribe_pack(user_id=user_id, pack_id=new_pack["id"], pinned=False)
    # 也订阅原组，便于「已加入」状态
    try:
        subscribe_pack(user_id=user_id, pack_id=pack_id, pinned=False)
    except Exception:
        pass
    out = get_pack(new_pack["id"]) or new_pack
    return {**out, "forked_from": pack_id, "copied": added}


def subscribe_pack(*, user_id: str, pack_id: str, pinned: bool = False) -> Dict[str, Any]:
    now = iso()
    db.execute(
        """
        INSERT INTO user_pack_subscriptions(user_id, pack_id, pinned, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, pack_id) DO UPDATE SET pinned = excluded.pinned
        """,
        (user_id, pack_id, 1 if pinned else 0, now),
    )
    return {"ok": True, "pack_id": pack_id, "pinned": bool(pinned)}


def delete_pack(*, user_id: str, pack_id: str) -> Dict[str, Any]:
    """删除账号下自己的表情组（不删媒体文件，避免误伤共用）。"""
    pack = get_pack(pack_id)
    if pack is None:
        raise ValueError("组不存在")
    if (pack.get("scope") or "") != "account":
        raise ValueError("只能删除「我的」表情组")
    if (pack.get("owner_user_id") or "") != user_id:
        raise ValueError("无权删除该组")
    db.execute("DELETE FROM stickers WHERE pack_id = ?", (pack_id,))
    db.execute("DELETE FROM user_pack_subscriptions WHERE pack_id = ?", (pack_id,))
    db.execute(
        "DELETE FROM sticker_packs WHERE id = ? AND scope = 'account' AND owner_user_id = ?",
        (pack_id, user_id),
    )
    return {"ok": True, "pack_id": pack_id}


def is_subscribed(*, user_id: str, pack_id: str) -> bool:
    row = db.fetchone(
        """
        SELECT 1 AS n FROM user_pack_subscriptions
        WHERE user_id = ? AND pack_id = ?
        """,
        (user_id, pack_id),
    )
    return row is not None


def media_readable_by_user(*, file_id: str, user_id: str) -> bool:
    """本账号媒体，或挂在 global 表情组上的贡献者媒体，可读。"""
    own = db.fetchone(
        "SELECT id FROM media_files WHERE id = ? AND user_id = ?",
        (file_id, user_id),
    )
    if own is not None:
        return True
    hit = db.fetchone(
        """
        SELECT 1 AS n FROM stickers s
        JOIN sticker_packs p ON p.id = s.pack_id
        WHERE s.file_id = ? AND p.scope = 'global'
        LIMIT 1
        """,
        (file_id,),
    )
    return hit is not None


def resolve_media_row(*, file_id: str, user_id: str):
    """可读则返回 media_files 行（可能属其他用户）。"""
    if not media_readable_by_user(file_id=file_id, user_id=user_id):
        return None
    return db.fetchone("SELECT * FROM media_files WHERE id = ?", (file_id,))
