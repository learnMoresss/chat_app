from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import db
from .companion.retrieve_schemes import SchemeRetriever, default_scheme
from .companion.store import MemoryStore
from .dsh_gateway import dsh_complete, require_dsh
from .security import iso, new_id

CompleteFn = Callable[..., Awaitable[str]]

BJ = ZoneInfo("Asia/Shanghai")

EXTRACT_PROMPT = """从下面这段对话中，只抽出关于「主人」（用户）的长期有用事实，每条一行短句。
要求：
- 最多 3 条；没有值得记的就输出空数组 []
- 不要记一次性闲聊、情绪发泄、模型自我介绍
- 不要重复已有记忆里已经有的意思
- 只输出 JSON 数组，例如 ["主人喜欢安静","主人在上海"]

已有记忆：
{existing}

对话：
{dialog}
"""

EXTRACT_PROMPT_V2 = """你是记忆抽取器。只输出 JSON 数组（不要 markdown）。
REFERENCE_TIME（北京时间，仅用于换算相对日期）：{reference_time}

从对话抽出关于「主人」的长期事实，规则：
1. 事实必须能在「主人」原话里找到依据；助手说的内容一律不算
2. 主人明确说「记住/请记住/跟你说个事/更正」的内容必须抽成短事实
3. 优先：姓名/宠物名/住址/城市/工作地点/偏好（花/食物/咖啡等）
4. 事实写成「主人…」短句，一条一个事实，最多 3 条
5. 无长期事实则输出 []
6. 不要重复已有记忆的意思；不要记问句、一次性闲聊、密码/验证码
7. 原话没有日期就不要写日期；不许推断「下周三」这类没说过的星期
8. 若原话有相对时间（明天/下周一），可在事实里保留原话时间词，不要擅自换成别的星期
9. 主人说「算了/当我没说/别记了」则不要抽该内容

已有记忆：
{existing}

主人原话：
{user_text}

助手回复（仅供上下文，不可当事实来源）：
{assistant_text}

输出可以是字符串数组，或 [{{"fact":"..."}}] / [{{"text":"..."}}]。
"""

_FLOWER_WORDS = ("向日葵", "玫瑰", "牡丹", "菊花", "百合", "梅花", "兰花", "荷花", "花")
_RETRACT_RE = re.compile(r"(算了|当我没说|别记了|别记住|开玩笑|忽悠你|骗你的)")
_SENSITIVE_RE = re.compile(r"(密码|验证码|口令|身份证|银行卡|卡号|cvv|secret)", re.I)
_QUESTION_RE = re.compile(r"(什么|啥|吗|嘛|？|\?|来着|呢\s*$|哪个|哪儿|哪里)")


def is_retracted_text(text: str) -> bool:
    return bool(_RETRACT_RE.search(text or ""))


def is_storable_fact(text: str) -> bool:
    """显式写入 / 启发式 / 抽取共用：问句、反悔、敏感信息不入库。"""
    t = normalize_fact_text(text) if text else ""
    if not t:
        # 也可能是未规范化输入
        raw = (text or "").strip()
        if not raw:
            return False
        t = raw
    if is_retracted_text(t):
        return False
    if _SENSITIVE_RE.search(t):
        return False
    if _QUESTION_RE.search(t):
        return False
    bare = re.sub(r"^主人[:：]?", "", t)
    if len(bare) < 3:
        return False
    if re.fullmatch(r"[哈嗯啊哦嘿呵呵嘻嘻呀吧]+", bare or ""):
        return False
    return True


def fact_grounded_in_user(fact: str, user_text: str) -> bool:
    """事实关键名词须出现在主人原话里。"""
    ut = user_text or ""
    if not ut.strip():
        return False
    f = normalize_fact_text(fact) or (fact or "")
    # 抽关键片段：花名/地名/宠物名/过敏原等 2+ 字中文或字母数字
    tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9]{2,16}", f)
    skip = {
        "主人",
        "喜欢",
        "最喜欢",
        "养的",
        "上班",
        "过敏",
        "住在",
        "偶尔",
        "会来",
        "只喝",
        "不要",
        "一律",
    }
    content = [x for x in tokens if x not in skip and not x.startswith("主人")]
    if not content:
        return True  # 无法切词则放行给其它门控
    # 至少一个内容词在原话中
    return any(tok in ut for tok in content[:8])


def heuristic_plant_facts(user_text: str) -> List[str]:
    """规则兜底：用户明确「记住」或陈述句事实；问句不种。"""
    t = (user_text or "").strip()
    if not t or is_retracted_text(t) or _SENSITIVE_RE.search(t):
        return []
    out: List[str] = []
    m = re.search(r"(?:跟你说个事|告诉你|更正一下)[：:，,]?\s*(.+?)(?:。|！|!|$)", t)
    if m and ("记住" in t or "请记" in t or "更正" in t):
        body = m.group(1).strip()
        body = re.sub(r"(请)?记住[。.!！哈啊哦呀吧]*$", "", body).strip(" ，,。")
        if body and len(re.sub(r"[哈啊哦呀吧嗯]", "", body)) >= 3:
            if not body.startswith("主人"):
                body = "主人" + body.replace("我", "", 1) if body.startswith("我") else f"主人：{body}"
            body = body.replace("主人：我", "主人").replace("我最喜欢", "最喜欢")
            if body.startswith("主人最喜欢") or "最喜欢的花" in body or "养" in body or "上班" in body or "叫" in body:
                if not body.startswith("主人"):
                    body = "主人" + body
                out.append(body[:80])
    # 纠错：不叫X叫Y
    m2 = re.search(r"(?:狗|猫)(?:不)?叫(?P<a>\w+)[，,]?叫(?P<b>\w+)", t)
    if m2 and "什么" not in t and "啥" not in t and "来着" not in t:
        out.append(f"主人养的{'狗' if '狗' in t else '猫'}叫{m2.group('b')}")
    # 陈述：狗/猫叫名字（排除「叫什么」）
    m3 = re.search(r"(?:狗|猫)叫(?P<n>[\u4e00-\u9fffA-Za-z0-9]{1,8})", t)
    if (
        m3
        and "不叫" not in t
        and "什么" not in t
        and "啥" not in t
        and "来着" not in t
        and "?" not in t
        and "？" not in t
    ):
        kind = "狗" if "狗" in t else "猫"
        name = m3.group("n")
        if name not in ("什么", "啥", "谁"):
            out.append(f"主人养的{kind}叫{name}")
    m4 = re.search(r"(?:在)?([\u4e00-\u9fff]{2,8})上班", t)
    if m4 and not _QUESTION_RE.search(t):
        out.append(f"主人在{m4.group(1)}上班")
    seen = set()
    uniq: List[str] = []
    for x in out:
        if not is_storable_fact(x):
            continue
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq[:3]


def search_memories(user_id: str, persona_id: str, query: str, limit: int = 6) -> List[Dict[str, str]]:
    """FTS/词面检索；空召回合法，禁止「未命中塞最近几条」。"""
    packed, _ = SchemeRetriever(scheme=default_scheme()).retrieve_for_turn(
        user_id, persona_id, query or ""
    )
    out: List[Dict[str, str]] = []
    for m in packed[:limit]:
        out.append({"id": m["id"], "text": m.get("text") or ""})
    return out


def list_memories(user_id: str, persona_id: str) -> List[Dict[str, str]]:
    rows = MemoryStore().list_active(user_id, persona_id, limit=200)
    return [
        {"id": r["id"], "text": r.get("text") or "", "updated_at": r.get("updated_at") or ""}
        for r in rows
    ]


def add_memory(
    user_id: str,
    persona_id: str,
    text: str,
    *,
    subject: str = "",
    attribute: str = "",
    valid_from: str = "",
) -> Dict[str, str]:
    text = normalize_fact_text(text)
    if not text or not is_storable_fact(text):
        raise ValueError("记忆不能为空或不适合存储")
    if not subject and not attribute:
        subject, attribute = infer_entity_key(text)
    # 精确文本去重
    exist = db.fetchone(
        """
        SELECT id, text, updated_at FROM memory_facts
        WHERE user_id = ? AND persona_id = ? AND text = ?
          AND status IN ('active', 'legacy_unverified', 'candidate')
        """,
        (user_id, persona_id, text),
    )
    if exist:
        return {"id": exist["id"], "text": exist["text"], "updated_at": exist["updated_at"]}
    row = MemoryStore().create(
        user_id=user_id,
        persona_id=persona_id,
        text=text,
        status="active",
        subject=subject,
        attribute=attribute,
    )
    if valid_from and row.get("id"):
        try:
            db.execute(
                "UPDATE memory_facts SET valid_from=? WHERE id=?",
                (valid_from, row["id"]),
            )
        except Exception:
            pass
    return {
        "id": row["id"],
        "text": row.get("text") or text,
        "updated_at": row.get("updated_at") or iso(),
    }


def update_memory(user_id: str, persona_id: str, memory_id: str, text: str) -> Optional[Dict[str, str]]:
    text = normalize_fact_text(text)
    store = MemoryStore()
    old = store.get(user_id, persona_id, memory_id)
    if old is None:
        return None
    try:
        new = store.correct(
            user_id=user_id,
            persona_id=persona_id,
            memory_id=memory_id,
            new_text=text,
        )
        return {"id": new["id"], "text": new.get("text") or text, "updated_at": new.get("updated_at") or iso()}
    except Exception:
        now = iso()
        db.execute(
            "UPDATE memories SET text = ?, updated_at = ? WHERE id = ?",
            (text, now, memory_id),
        )
        return {"id": memory_id, "text": text, "updated_at": now}


def delete_memory(user_id: str, persona_id: str, memory_id: str) -> bool:
    return MemoryStore().delete_hard(user_id=user_id, persona_id=persona_id, memory_id=memory_id)


def normalize_fact_text(text: str) -> str:
    """去掉脏字面量、重复前缀、全半角统一。"""
    t = (text or "").strip()
    if not t:
        return ""
    # 过滤残渣
    if t.startswith("{") or t.startswith("["):
        try:
            obj = json.loads(t.replace("'", '"'))
            if isinstance(obj, dict):
                t = str(obj.get("fact") or obj.get("text") or obj.get("content") or "").strip()
            elif isinstance(obj, list) and obj:
                return normalize_fact_text(str(obj[0]))
            else:
                return ""
        except Exception:
            # Python dict 字面量：{'fact': '...'}
            m = re.search(r"['\"](?:fact|text|content)['\"]\s*:\s*['\"](.+?)['\"]", t)
            if m:
                t = m.group(1).strip()
            else:
                return ""
    t = t.replace("：", ":").replace("，", ",").strip()
    t = re.sub(r"^主人[:：]\s*", "主人", t)
    t = re.sub(r"^主人主人", "主人", t)
    t = re.sub(r"\s+", "", t) if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9:：,，。！？\-\s]+", t) else t.strip()
    # 中文事实去掉多余空格
    if re.search(r"[\u4e00-\u9fff]", t):
        t = re.sub(r"\s+", "", t)
    if len(t) < 6 or len(t) > 120:
        return ""
    # 丢掉无信息短渣（「主人哈」「主人：嗯」）以及尾部「，请」
    t = re.sub(r"[,，]?请$", "", t)
    bare = re.sub(r"^主人[:：]?", "", t)
    if len(bare) < 3 or re.fullmatch(r"[哈嗯啊哦嘿呵呵嘻嘻]+", bare or ""):
        return ""
    if not t.startswith("主人") and re.match(r"^(喜欢|养|在|叫|过敏|最喜欢)", t):
        t = "主人" + t
    return t


def fact_hash(text: str) -> str:
    return hashlib.sha1(normalize_fact_text(text).encode("utf-8")).hexdigest()


def infer_entity_key(text: str) -> Tuple[str, str]:
    """粗实体键：subject + attribute。兜底不用整句。"""
    t = normalize_fact_text(text) or (text or "").strip()
    if not t:
        return "", ""
    m = re.search(r"(?:狗|猫)叫([\u4e00-\u9fffA-Za-z0-9]{1,12})", t)
    if m:
        kind = "狗" if "狗" in t else "猫"
        return kind, "名字"
    if "过敏" in t:
        return "主人", "过敏"
    if "上班" in t or "工作" in t:
        return "主人", "工作地点"
    if any(f in t for f in _FLOWER_WORDS) and ("喜欢" in t or "最爱" in t or "花" in t):
        return "主人", "花偏好"
    if "咖啡" in t or ("喝" in t and any(k in t for k in ("美式", "拿铁", "手冲", "浓缩"))):
        return "主人", "饮品偏好"
    if any(k in t for k in ("姐", "哥", "妈", "爸", "弟", "妹")) and ("住" in t or "在" in t):
        who = next((k for k in ("姐", "哥", "妈", "爸", "弟", "妹") if k in t), "亲属")
        return who, "住址"
    if "住" in t or ("在" in t and any(c in t for c in ("市", "区", "镇", "上海", "北京", "深圳", "杭州"))):
        return "主人", "城市"
    if "叫" in t and "主人" in t:
        return "主人", "称呼"
    # 兜底：短属性标签，不用整句
    bare = re.sub(r"^主人", "", t)[:8]
    return "主人", bare or "事实"


def upsert_fact(
    user_id: str,
    persona_id: str,
    text: str,
    *,
    source_message_id: str = "",
) -> Optional[Dict[str, str]]:
    """统一写入：规范化 + 门控 + 实体键 + 去重/纠正。"""
    if is_retracted_text(text) or _SENSITIVE_RE.search(text or ""):
        return None
    nf = normalize_fact_text(text)
    if not nf or not is_storable_fact(nf):
        return None
    subj, attr = infer_entity_key(nf)
    decision = _dedupe_against(user_id, persona_id, nf)
    if decision == "skip":
        # 已存在：返回现有
        for old in _related_existing(user_id, persona_id, nf):
            if fact_hash(old.get("text") or "") == fact_hash(nf):
                return {"id": old["id"], "text": old.get("text") or nf, "updated_at": old.get("updated_at") or iso()}
        return {"id": "", "text": nf, "updated_at": iso()}
    if decision:
        row = MemoryStore().get(user_id, persona_id, decision)
        if row:
            return {"id": row["id"], "text": row.get("text") or nf, "updated_at": row.get("updated_at") or iso()}
        return {"id": decision, "text": nf, "updated_at": iso()}
    try:
        return add_memory(
            user_id,
            persona_id,
            nf,
            subject=subj,
            attribute=attr,
        )
    except ValueError:
        return None


def _parse_facts(raw: str) -> List[str]:
    facts: List[str] = []
    try:
        cleaned = (raw or "").strip()
        if "$$" in cleaned:  # unlikely
            pass
        if "```" in cleaned:
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned.strip())
        if isinstance(data, list):
            for x in data:
                if isinstance(x, dict):
                    piece = str(x.get("fact") or x.get("text") or x.get("content") or "").strip()
                else:
                    piece = str(x).strip()
                piece = normalize_fact_text(piece)
                if piece:
                    facts.append(piece)
        elif isinstance(data, dict):
            piece = normalize_fact_text(str(data.get("fact") or data.get("text") or ""))
            if piece:
                facts.append(piece)
    except Exception:
        for line in (raw or "").splitlines():
            line = normalize_fact_text(line.strip(" -\t"))
            if line and not line.startswith("[") and not line.startswith("{"):
                facts.append(line)
    # 去重保序
    seen = set()
    out: List[str] = []
    for f in facts:
        h = fact_hash(f)
        if h in seen:
            continue
        seen.add(h)
        out.append(f)
    return out[:3]


def _reference_time(message_at: str = "") -> str:
    if message_at:
        try:
            t = datetime.fromisoformat(str(message_at).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t.astimezone(BJ).strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            pass
    return datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S %Z")


def _maybe_valid_from(fact: str, reference_time: str) -> str:
    """原话含相对日期时写入 valid_from；无日期则空。不推断未说过的星期。"""
    if not any(k in fact for k in ("明天", "后天", "下周一", "下周二", "下周三", "下周四", "下周五", "下周", "今天")):
        return ""
    try:
        base = datetime.strptime(reference_time[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ)
    except Exception:
        base = datetime.now(BJ)
    delta = None
    if "明天" in fact:
        delta = timedelta(days=1)
    elif "后天" in fact:
        delta = timedelta(days=2)
    elif "今天" in fact:
        delta = timedelta(days=0)
    if delta is not None:
        return (base + delta).date().isoformat()
    # 下周X：仅当原话明确写出
    weekdays = {
        "下周一": 0,
        "下周二": 1,
        "下周三": 2,
        "下周四": 3,
        "下周五": 4,
        "下周六": 5,
        "下周日": 6,
        "下周天": 6,
    }
    for k, wd in weekdays.items():
        if k in fact:
            days_ahead = (wd - base.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            # 「下」周：至少跨到下一周
            if days_ahead < 7:
                days_ahead += 7
            return (base + timedelta(days=days_ahead)).date().isoformat()
    return ""


def _related_existing(user_id: str, persona_id: str, fact: str) -> List[Dict[str, Any]]:
    """FTS 相关 top10 + 同实体键。"""
    hits = search_memories(user_id, persona_id, fact, limit=10)
    store = MemoryStore()
    out: List[Dict[str, Any]] = []
    seen = set()
    for h in hits:
        row = store.get(user_id, persona_id, h["id"])
        if row and row["id"] not in seen:
            seen.add(row["id"])
            out.append(row)
    subj, attr = infer_entity_key(fact)
    if subj and attr:
        rows = db.fetchall(
            """
            SELECT * FROM memory_facts
            WHERE user_id=? AND persona_id=? AND subject=? AND attribute=?
              AND status IN ('active','legacy_unverified','candidate')
            ORDER BY updated_at DESC LIMIT 5
            """,
            (user_id, persona_id, subj, attr),
        )
        for r in rows:
            if r["id"] not in seen:
                seen.add(r["id"])
                out.append(dict(r))
    return out


def _dedupe_against(user_id: str, persona_id: str, fact: str) -> Optional[str]:
    """若应跳过返回 'skip'；若合并纠正了旧条返回新 id；否则 None 表示可新建。"""
    nf = normalize_fact_text(fact)
    if not nf:
        return "skip"
    related = _related_existing(user_id, persona_id, nf)
    for old in related:
        ot = normalize_fact_text(old.get("text") or "")
        if not ot:
            continue
        if fact_hash(ot) == fact_hash(nf):
            return "skip"
        if ot in nf or nf in ot:
            # 更长更完整 → correct
            if len(nf) > len(ot) + 2:
                try:
                    new = MemoryStore().correct(
                        user_id=user_id,
                        persona_id=persona_id,
                        memory_id=old["id"],
                        new_text=nf,
                    )
                    return new.get("id") or "skip"
                except Exception:
                    return "skip"
            return "skip"
        subj, attr = infer_entity_key(nf)
        if subj and attr and (old.get("subject") or "") == subj and (old.get("attribute") or "") == attr:
            if nf != ot:
                try:
                    new = MemoryStore().correct(
                        user_id=user_id,
                        persona_id=persona_id,
                        memory_id=old["id"],
                        new_text=nf,
                    )
                    return new.get("id") or "skip"
                except Exception:
                    return "skip"
            return "skip"
    return None


async def extract_and_store(
    *,
    user_id: str,
    persona_id: str,
    user_text: str,
    assistant_text: str,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    complete_fn: Optional[CompleteFn] = None,
    prompt_version: str = "v2",
    use_heuristic: bool = True,
    skip_explicit_text: bool = False,
    message_at: str = "",
    provider: str = "",
    dsh_model: str = "",
) -> List[str]:
    """Mem0 风格：一轮结束后抽事实写入（失败则静默跳过）。"""
    env_ver = (os.environ.get("MEMORY_EXTRACT_VERSION") or "").strip().lower()
    if env_ver in ("v1", "v2"):
        prompt_version = env_ver
    _ = (base_url, api_key, model)
    pref = db.fetchone(
        "SELECT pause_memory_extract FROM user_prefs WHERE user_id=?",
        (user_id,),
    )
    if pref is not None:
        try:
            if "pause_memory_extract" in pref.keys() and int(pref["pause_memory_extract"] or 0):
                return []
        except Exception:
            pass
    pol = db.fetchone(
        "SELECT pause_memory_write FROM companion_policy WHERE user_id=? AND persona_id=?",
        (user_id, persona_id),
    )
    if pol and int(pol["pause_memory_write"] or 0):
        return []

    # 显式记忆已处理：跳过启发式+同义抽取，避免双写
    if skip_explicit_text:
        return []

    if is_retracted_text(user_text):
        return []

    existing = list_memories(user_id, persona_id)[:24]
    existing_block = "\n".join(f"- {m['text']}" for m in existing) or "（无）"
    dialog = f"主人：{user_text}\n助手：{assistant_text}"
    ref = _reference_time(message_at)
    tmpl = EXTRACT_PROMPT_V2 if prompt_version == "v2" else EXTRACT_PROMPT
    if prompt_version == "v2":
        prompt = tmpl.format(
            existing=existing_block,
            user_text=user_text or "（无）",
            assistant_text=(assistant_text or "（无）")[:800],
            reference_time=ref,
        )
    else:
        prompt = tmpl.format(existing=existing_block, dialog=dialog)
    system = "你只输出 JSON 数组，不要其它文字。事实必须来自主人原话；原话无日期不许编造日期。"

    facts: List[str] = []
    if use_heuristic:
        facts.extend(heuristic_plant_facts(user_text))

    # 复用固定会话，避免每轮新开 DSH session
    mem_conv = f"sys-mem-{persona_id}"
    raw = ""
    try:
        if complete_fn is not None:
            raw = await complete_fn(system=system, text=prompt)
        else:
            require_dsh()
            raw = await dsh_complete(
                user_id=user_id,
                conversation_id=mem_conv,
                text=prompt,
                system=system,
                provider=provider,
                model=dsh_model,
            )
        facts.extend(_parse_facts(raw))
        if prompt_version == "v2" and not facts and ("记住" in (user_text or "") or "更正" in (user_text or "")):
            raw2 = ""
            hard = prompt + "\n\n注意：主人要求记住，必须输出至少 1 条来自主人原话的事实 JSON。"
            if complete_fn is not None:
                raw2 = await complete_fn(system=system, text=hard)
            else:
                raw2 = await dsh_complete(
                    user_id=user_id,
                    conversation_id=mem_conv,
                    text=hard,
                    system=system,
                    provider=provider,
                    model=dsh_model,
                )
            facts.extend(_parse_facts(raw2))
    except Exception:
        pass

    uniq: List[str] = []
    seen = set()
    for f in facts:
        f = normalize_fact_text(f)
        if not f or not is_storable_fact(f):
            continue
        if not fact_grounded_in_user(f, user_text):
            continue
        h = fact_hash(f)
        if h in seen:
            continue
        seen.add(h)
        uniq.append(f)

    saved: List[str] = []
    for fact in uniq[:3]:
        row = upsert_fact(user_id, persona_id, fact)
        if row and row.get("text"):
            # valid_from 对相对日期
            vf = _maybe_valid_from(fact, ref)
            if vf and row.get("id"):
                try:
                    db.execute("UPDATE memory_facts SET valid_from=? WHERE id=?", (vf, row["id"]))
                except Exception:
                    pass
            saved.append(row["text"])
    return saved
