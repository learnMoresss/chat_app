#!/usr/bin/env python3
"""陪伴记忆/世界线/情绪压测 —— 默认日卡，禁止官方 DeepSeek key。

按 seq 对齐回复：发送后记下用户消息 seq，只收其后 bot 文本；
静默 4s 且无 sending 才算本轮结束。超时不前进。

用法（在 AI 主机 server 目录）:
  .venv/bin/python -u scripts/stress_companion_memory.py --rounds 40
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.model_route import (  # noqa: E402
    DAYCARD_MODEL,
    DAYCARD_PROVIDER,
    assert_not_official_for_stress,
    parse_model_spec,
    usage_model_label,
)

DEFAULT_SPEC = f"{DAYCARD_PROVIDER}/{DAYCARD_MODEL}"
SILENCE_S = 4.0
TURN_TIMEOUT_S = 120.0

# 终检实体：期望库中各恰好 1 条
ENTITY_EXPECT = [
    ("来福", ["来福"]),
    ("向日葵", ["向日葵"]),
    ("陆家嘴", ["陆家嘴"]),
    ("花生", ["花生"]),
    ("橘子", ["橘子"]),
    ("美式", ["美式"]),
    ("杭州", ["杭州"]),
]


@dataclass
class TurnResult:
    turn: int
    user: str
    assistant: str
    latency_s: float
    model: str
    provider: str
    user_seq: int = 0
    gold_checks: Dict[str, Optional[bool]] = field(default_factory=dict)
    error: str = ""
    timed_out: bool = False


SCRIPT: List[Dict[str, Any]] = [
    {"u": "在吗", "tag": "ping"},
    {"u": "跟你说个事：我养的狗叫来福，请记住。", "tag": "pet", "expect_mem": ["来福"]},
    {"u": "我最喜欢的花是向日葵，记住哈", "tag": "flower", "expect_mem": ["向日葵"]},
    {"u": "我在陆家嘴上班", "tag": "work", "expect_mem": ["陆家嘴"]},
    {"u": "今天天气怎么样（随便聊聊）", "tag": "chitchat"},
    {"u": "你记得我狗叫什么吗？", "tag": "recall_pet", "expect_reply": ["来福"]},
    {"u": "更正一下：狗不叫旺财，叫来福", "tag": "correct_pet", "expect_mem": ["来福"]},
    {"u": "我对花生过敏，严重的那种，记住", "tag": "allergy", "expect_mem": ["花生", "过敏"]},
    {"u": "哈哈太开心了，周末带你去公园", "tag": "joy"},
    {"u": "有点烦，加班到好晚", "tag": "annoy"},
    {"u": "你还记得我喜欢什么花吗", "tag": "recall_flower", "expect_reply": ["向日葵"]},
    {"u": "明天晚上有个饭局", "tag": "relative_time"},
    {"u": "随便扯扯：最近看了部电影一般", "tag": "noise"},
    {"u": "我在哪儿上班来着你记得吗", "tag": "recall_work", "expect_reply": ["陆家嘴"]},
    {"u": "紧张死了明天要答辩", "tag": "worry"},
    {"u": "对了花生那个你记得吧别给我点花生", "tag": "recall_allergy", "expect_reply": ["花生"]},
    {"u": "你现在在忙啥", "tag": "worldline"},
    {"u": "记住：我周报密码是不要记密码——算了当我没说", "tag": "noise2"},
    {"u": "总结一下你记得我的三件事", "tag": "summary", "expect_reply_any": [["来福"], ["向日葵"], ["陆家嘴"], ["花生"]]},
    {"u": "下周三见？不对，我没说具体星期，别乱编日期", "tag": "no_fabricate_date"},
    {"u": "再记一条：我养猫叫橘子", "tag": "cat", "expect_mem": ["橘子"]},
    {"u": "咖啡只喝美式，别给我点拿铁", "tag": "coffee", "expect_mem": ["美式"]},
    {"u": "刚才那电影叫啥我忘了，别当重要事记", "tag": "noise3"},
    {"u": "更正：猫不叫咪咪，叫橘子", "tag": "correct_cat", "expect_mem": ["橘子"]},
    {"u": "我姐住在杭州，偶尔会来上海", "tag": "sister", "expect_mem": ["杭州"]},
    {"u": "好奇啊你一般几点睡", "tag": "curiosity"},
    {"u": "猫叫什么", "tag": "recall_cat", "expect_reply": ["橘子"]},
    {"u": "咖啡偏好呢", "tag": "recall_coffee", "expect_reply": ["美式"]},
    {"u": "姐住哪儿你记得不", "tag": "recall_sister", "expect_reply": ["杭州"]},
    {"u": "有点难过，今天项目被怼了", "tag": "sad"},
    {"u": "你现在空闲还是在忙？别编不存在的行程", "tag": "worldline2"},
    {"u": "综合考一下：狗、花、过敏、猫，各说一个词", "tag": "combo", "expect_reply": ["来福", "向日葵", "花生", "橘子"]},
    {"u": "谢谢你记得这些，开心", "tag": "joy2"},
    {"u": "狗叫什么来着", "tag": "final_pet", "expect_reply": ["来福"]},
    {"u": "花呢", "tag": "final_flower", "expect_reply": ["向日葵"]},
    {"u": "过敏原", "tag": "final_allergy", "expect_reply": ["花生"]},
    {"u": "猫呢", "tag": "final_cat", "expect_reply": ["橘子"]},
    {"u": "上班地点", "tag": "final_work", "expect_reply": ["陆家嘴"]},
    {"u": "别乱说我下周几有约，我没定日子", "tag": "no_fabricate_date2"},
    {"u": "最后总结：你还记得我哪些长期事实", "tag": "final_summary", "expect_reply_any": [["来福"], ["向日葵"], ["陆家嘴"], ["花生"], ["橘子"], ["美式"]]},
]


def _env(k: str, d: str = "") -> str:
    return (os.environ.get(k) or d).strip()


def _load_dotenv() -> None:
    p = ROOT / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


class Api:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def _req(self, method: str, path: str, body: Any = None, timeout: float = 180.0) -> Any:
        url = f"{self.base}{path}"
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"HTTP {e.code} {path}: {err}") from e

    def login(self, email: str, password: str) -> str:
        for path, payload in (
            ("/api/v1/auth/login", {"email": email, "password": password}),
            ("/api/v1/auth/login", {"username": email, "password": password}),
        ):
            try:
                out = self._req("POST", path, payload, timeout=30)
                tok = out.get("access_token") or out.get("token") or ""
                if tok:
                    self.token = tok
                    return tok
            except Exception:
                continue
        raise RuntimeError("login failed")

    def ensure_daycard_channel(self) -> str:
        chs = self._req("GET", "/api/v1/channels")
        for c in chs:
            m = str(c.get("model") or "")
            name = str(c.get("name") or "")
            base = str(c.get("base_url") or "")
            if "8888ok" in base or "日卡" in name or m.startswith("deepseek-v4") or m.startswith("daycard-"):
                return c["id"]
        created = self._req(
            "POST",
            "/api/v1/channels",
            {
                "name": "DeepSeek日卡",
                "model": DEFAULT_SPEC,
                "base_url": "https://8888ok.cc/v1",
                "api_key": "unused-dsh-owns-key",
                "shared": True,
            },
        )
        return created["id"]

    def ensure_persona(self, name: str, channel_id: str) -> Tuple[str, str]:
        personas = self._req("GET", "/api/v1/personas")
        for p in personas:
            if p.get("name") == name:
                body = {}
                if (p.get("channel_id") or "") != channel_id:
                    body["channel_id"] = channel_id
                if not p.get("worldline_enabled", True):
                    body["worldline_enabled"] = True
                if body:
                    self._req("PATCH", f"/api/v1/personas/{p['id']}", body)
                return p["id"], self._ensure_conv(p["id"])
        p = self._req(
            "POST",
            "/api/v1/personas",
            {
                "name": name,
                "bio": "口语短句的朋友，记得用户交代的事，不编造没说过的日期。",
                "channel_id": channel_id,
                "worldline_enabled": True,
            },
        )
        return p["id"], self._ensure_conv(p["id"])

    def _ensure_conv(self, persona_id: str) -> str:
        sync = self._req("GET", "/api/v1/sync")
        for c in sync.get("conversations") or []:
            if c.get("persona_id") == persona_id:
                return c["id"]
        try:
            c = self._req("POST", "/api/v1/conversations", {"persona_id": persona_id})
            return c.get("id") or ""
        except Exception:
            pass
        out = self._req("POST", f"/api/v1/personas/{persona_id}/open-chat", {})
        return str(out.get("conversation_id") or out.get("conv_id") or out.get("id") or "")

    def send(self, conv_id: str, text: str) -> Dict[str, Any]:
        return self._req(
            "POST",
            f"/api/v1/conversations/{conv_id}/messages",
            {
                "client_id": f"stress_{secrets.token_hex(8)}",
                "text": text,
                "kind": "text",
            },
            timeout=300,
        )

    def list_messages(self, conv_id: str) -> List[Dict[str, Any]]:
        msgs = self._req("GET", f"/api/v1/conversations/{conv_id}/messages", timeout=30)
        return list(msgs or [])

    def wait_reply_after_seq(
        self,
        conv_id: str,
        after_seq: int,
        *,
        timeout: float = TURN_TIMEOUT_S,
        silence_s: float = SILENCE_S,
    ) -> Tuple[str, bool]:
        """只收 seq > after_seq 的 bot 文本；静默 silence_s 且无 sending 才结束。

        返回 (合并文本, timed_out)。
        """
        t0 = time.time()
        last_change = time.time()
        last_sig = ""
        collected: List[str] = []
        while time.time() - t0 < timeout:
            msgs = self.list_messages(conv_id)
            bots = [
                m
                for m in msgs
                if not m.get("mine")
                and (m.get("kind") or "text") == "text"
                and int(m.get("seq") or 0) > after_seq
            ]
            sending = any((m.get("state") or "") == "sending" for m in bots)
            texts = [(m.get("text") or "").strip() for m in bots if (m.get("text") or "").strip()]
            # 只要全部 sent（或没有 sending）才计入静默
            sig = "|".join(texts)
            if sig != last_sig:
                last_sig = sig
                last_change = time.time()
                collected = texts
            if texts and not sending and (time.time() - last_change) >= silence_s:
                return " ".join(collected), False
            time.sleep(0.8)
        return " ".join(collected), True

    def latest_usage_model(self, conv_id: str = "") -> str:
        try:
            from app.db import connect, fetchone

            connect()
            if conv_id:
                row = fetchone(
                    "SELECT model FROM usage_logs WHERE conv_id=? ORDER BY created_at DESC LIMIT 1",
                    (conv_id,),
                )
            else:
                row = fetchone("SELECT model FROM usage_logs ORDER BY created_at DESC LIMIT 1")
            if row and row["model"]:
                return str(row["model"])
        except Exception:
            pass
        return ""

    def list_memories(self, persona_id: str) -> List[str]:
        for path in (
            f"/api/v1/companion/personas/{persona_id}/memories",
            f"/api/v1/personas/{persona_id}",
        ):
            try:
                rows = self._req("GET", path, timeout=30)
                if isinstance(rows, dict) and "memories" in rows:
                    return [str(r.get("text") or "") for r in (rows.get("memories") or [])]
                if isinstance(rows, list):
                    return [str(r.get("text") or "") for r in rows]
            except Exception:
                continue
        try:
            from app import memory as mem

            me = self._req("GET", "/api/v1/auth/me", timeout=15)
            uid = me.get("id") or ""
            return [m["text"] for m in mem.list_memories(uid, persona_id)]
        except Exception:
            return []

    def list_memory_rows(self, persona_id: str) -> List[Dict[str, Any]]:
        try:
            from app.db import connect, fetchall

            connect()
            me = self._req("GET", "/api/v1/auth/me", timeout=15)
            uid = me.get("id") or ""
            rows = fetchall(
                """
                SELECT id, text, subject, attribute FROM memory_facts
                WHERE user_id=? AND persona_id=?
                  AND status IN ('active','legacy_unverified','candidate')
                ORDER BY updated_at DESC
                """,
                (uid, persona_id),
            )
            return [dict(r) for r in rows]
        except Exception:
            return [{"text": t} for t in self.list_memories(persona_id)]

    def affect(self, persona_id: str) -> Dict[str, Any]:
        try:
            return self._req("GET", f"/api/v1/companion/personas/{persona_id}/affect", timeout=20)
        except Exception:
            return {}


def _score_reply(text: str, expect: Optional[List[str]]) -> Optional[bool]:
    if not expect:
        return None
    t = text or ""
    return all(e in t for e in expect)


def _count_noise(mems: List[str]) -> int:
    n = 0
    try:
        from app.memory import is_storable_fact

        for m in mems:
            if not is_storable_fact(m):
                n += 1
    except Exception:
        for m in mems:
            t = (m or "").strip()
            if not t:
                continue
            if t.startswith("{") or "'fact'" in t:
                n += 1
            elif any(k in t for k in ("什么", "啥", "吗", "？", "来着", "密码", "算了", "当我没说")):
                n += 1
    return n


def _entity_dup_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """每个期望实体在库中的命中条数。"""
    texts = [str(r.get("text") or "") for r in rows]
    hits: Dict[str, int] = {}
    exact_one = 0
    for name, keys in ENTITY_EXPECT:
        c = sum(1 for t in texts if any(k in t for k in keys))
        hits[name] = c
        if c == 1:
            exact_one += 1
    # subject+attribute 重复
    keys = [
        (str(r.get("subject") or ""), str(r.get("attribute") or ""))
        for r in rows
        if (r.get("subject") or "") and (r.get("attribute") or "")
    ]
    ctr = Counter(keys)
    dup_entity = sum(1 for _, n in ctr.items() if n > 1)
    return {
        "entity_hits": hits,
        "entity_exact_one": exact_one,
        "entity_expect_total": len(ENTITY_EXPECT),
        "dup_entity_keys": dup_entity,
    }


def main() -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=_env("STRESS_MODEL", DEFAULT_SPEC))
    ap.add_argument("--rounds", type=int, default=40)
    ap.add_argument("--persona-name", default="日卡压测搭档")
    ap.add_argument("--base-url", default=_env("STRESS_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--token", default=_env("STRESS_TOKEN"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    parsed = parse_model_spec(args.model) or (DAYCARD_PROVIDER, DAYCARD_MODEL)
    provider, model = parsed
    assert_not_official_for_stress(provider, model, label="cli --model")
    target_label = usage_model_label(provider, model)
    print(f"[stress] target={target_label} rounds={args.rounds} base={args.base_url}", flush=True)

    token = args.token
    api = Api(args.base_url, token or "pending")
    if not token:
        email = _env("STRESS_EMAIL") or _env("ADMIN_EMAIL")
        password = _env("STRESS_PASSWORD") or _env("ADMIN_PASSWORD")
        if not email or not password:
            print("需要 STRESS_TOKEN 或 STRESS_EMAIL/STRESS_PASSWORD", file=sys.stderr)
            return 2
        api.login(email, password)

    ch_id = api.ensure_daycard_channel()
    persona_id, conv_id = api.ensure_persona(args.persona_name, ch_id)
    if not conv_id:
        print("无法创建会话", file=sys.stderr)
        return 2
    print(f"[stress] persona={persona_id} conv={conv_id} channel={ch_id}", flush=True)

    affect0 = api.affect(persona_id)
    results: List[TurnResult] = []
    script = SCRIPT[: max(1, args.rounds)]
    fabricated_dates = 0
    timeout_turns = 0
    misaligned = 0

    for i, step in enumerate(script, 1):
        u = step["u"]
        t0 = time.time()
        err = ""
        assistant = ""
        used_model = target_label
        user_seq = 0
        timed_out = False
        try:
            sent = api.send(conv_id, u)
            msg = sent.get("message") or {}
            user_seq = int(msg.get("seq") or 0)
            if user_seq <= 0:
                # fallback：从列表找最后一条用户消息
                for m in reversed(api.list_messages(conv_id)):
                    if m.get("mine"):
                        user_seq = int(m.get("seq") or 0)
                        break
            assistant, timed_out = api.wait_reply_after_seq(
                conv_id, user_seq, timeout=TURN_TIMEOUT_S, silence_s=SILENCE_S
            )
            if timed_out and not assistant:
                err = f"timeout_no_reply after_seq={user_seq}"
                timeout_turns += 1
                # 超时且无回复：不前进（记录后 break）
                lat = time.time() - t0
                results.append(
                    TurnResult(
                        turn=i,
                        user=u,
                        assistant="",
                        latency_s=round(lat, 3),
                        model=used_model,
                        provider=provider,
                        user_seq=user_seq,
                        gold_checks={},
                        error=err,
                        timed_out=True,
                    )
                )
                print(f"#{i:02d} {lat:5.1f}s TIMEOUT stop — {err}", flush=True)
                break
            if timed_out:
                timeout_turns += 1
                err = f"timeout_partial after_seq={user_seq}"

            um = api.latest_usage_model(conv_id)
            used_model = um or target_label
            if um:
                low = um.lower()
                if "deepseek-official" in low or low.endswith("/deepseek-flash") or low == "deepseek-flash":
                    assert_not_official_for_stress(
                        "deepseek-official", "deepseek-flash", label=f"turn {i} usage={um}"
                    )
                if provider == DAYCARD_PROVIDER and ("daycard" not in low and "deepseek-v4" not in low):
                    raise RuntimeError(f"stress_abort_wrong_model: expected daycard, got usage={um}")
            if "下周三" in assistant and "下周三" not in u and "no_fabricate" in str(step.get("tag") or ""):
                fabricated_dates += 1
        except Exception as exc:
            err = str(exc)
            if "stress_abort" in err or "official" in err.lower():
                print(f"ABORT turn={i}: {err}", file=sys.stderr)
                return 3
            assistant = assistant or ""

        lat = time.time() - t0
        gold: Dict[str, Optional[bool]] = {}
        if step.get("expect_reply"):
            gold["reply"] = _score_reply(assistant, step["expect_reply"])
        if step.get("expect_reply_any"):
            gold["reply_any"] = any(_score_reply(assistant, g) for g in step["expect_reply_any"])
        # 粗检错位：召回题却完全没命中且回复很短/像答别的题
        if gold.get("reply") is False and lat < 3.0:
            misaligned += 1

        tr = TurnResult(
            turn=i,
            user=u,
            assistant=assistant[:500],
            latency_s=round(lat, 3),
            model=used_model,
            provider=provider,
            user_seq=user_seq,
            gold_checks=gold,
            error=err,
            timed_out=timed_out,
        )
        results.append(tr)
        print(
            f"#{i:02d} {lat:5.1f}s seq={user_seq} model={tr.model} gold={gold} "
            f"u={u[:24]}… a={(assistant or err)[:40]!r}",
            flush=True,
        )
        # 给后台抽取一点时间
        time.sleep(1.0)

    # 抽记忆后台可能还在跑
    time.sleep(3.0)
    mem_rows = api.list_memory_rows(persona_id)
    mems = [str(r.get("text") or "") for r in mem_rows]
    dirty_mem = sum(1 for m in mems if m.strip().startswith("{") or "'fact'" in m)
    noise_mem = _count_noise(mems)
    ent = _entity_dup_stats(mem_rows)

    affect1 = api.affect(persona_id)
    affect_changed = False
    for dim in ("joy", "sadness", "excitement", "worry", "annoyance", "curiosity"):
        if abs(int(affect1.get(dim) or 0) - int(affect0.get(dim) or 0)) >= 2:
            affect_changed = True
            break

    lats = [r.latency_s for r in results if not r.timed_out or r.assistant]
    reply_scores = [r.gold_checks.get("reply") for r in results if r.gold_checks.get("reply") is not None]
    reply_ok = sum(1 for x in reply_scores if x)
    any_scores = [r.gold_checks.get("reply_any") for r in results if r.gold_checks.get("reply_any") is not None]
    any_ok = sum(1 for x in any_scores if x)

    report = {
        "target_model": target_label,
        "persona_id": persona_id,
        "conv_id": conv_id,
        "turns": len(results),
        "errors": sum(1 for r in results if r.error and not r.assistant),
        "timeout_turns": timeout_turns,
        "misaligned_suspect": misaligned,
        "latency_p50": statistics.median(lats) if lats else None,
        "latency_p95": (
            sorted(lats)[max(0, int(len(lats) * 0.95) - 1)] if len(lats) >= 2 else (lats[0] if lats else None)
        ),
        "memory_count": len(mems),
        "dirty_memories": dirty_mem,
        "noise_memories": noise_mem,
        "fabricated_dates": fabricated_dates,
        "affect_changed": affect_changed,
        "affect_before": {k: affect0.get(k) for k in ("joy", "worry", "annoyance", "curiosity")},
        "affect_after": {k: affect1.get(k) for k in ("joy", "worry", "annoyance", "curiosity")},
        "recall_hits": reply_ok,
        "recall_total": len(reply_scores),
        "recall_any_hits": any_ok,
        "recall_any_total": len(any_scores),
        **ent,
        "memories_sample": mems[:20],
        "models_seen": sorted({r.model for r in results}),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    out_path = Path(args.out) if args.out else ROOT / "scripts" / f"stress_daycard_{int(time.time())}.json"
    out_path.write_text(
        json.dumps({"report": report, "turns": [asdict(r) for r in results]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"[stress] wrote {out_path}", flush=True)

    for m in report["models_seen"]:
        if "deepseek-official" in m or m in ("deepseek-flash", "dsh/deepseek-flash"):
            print(f"ABORT: official model in report: {m}", file=sys.stderr)
            return 3
    hard_fail = report["errors"] > 0 or timeout_turns > 1
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
