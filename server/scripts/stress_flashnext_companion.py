#!/usr/bin/env python3
"""Qwen3.8 Flash Next × companion 记忆/情绪极限压测。

不改线上 DSH：对话与抽记忆走 llama-swap Flash Next；
读写记忆/情绪走现有 companion 栈。

用法（在 server 目录）:
  .venv/bin/python scripts/stress_flashnext_companion.py [--smoke|--full]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app import memory as mem  # noqa: E402
from app.companion.affect import AffectService  # noqa: E402
from app.companion.retriever import MemoryRetriever  # noqa: E402
from app.companion.snapshot import build_companion_snapshot  # noqa: E402
from app.companion.store import MemoryStore  # noqa: E402
from app.memory import EXTRACT_PROMPT  # noqa: E402
from app.security import iso, new_id  # noqa: E402

MODEL_DEFAULT = "qwen3.8-flash-next-iq3_xxs"


@dataclass
class TurnRecord:
    scenario: str
    turn: int
    user_text: str
    assistant_text: str
    latency_s: float
    extract_latency_s: float
    extracted: List[str]
    packed_ids: List[str]
    packed_texts: List[str]
    gold_hit_retrieve: Optional[bool] = None
    gold_hit_inject: Optional[bool] = None
    gold_hit_reply: Optional[bool] = None
    empty_ok: Optional[bool] = None
    affect: Dict[str, Any] = field(default_factory=dict)
    affect_hint_present: bool = False
    error: str = ""


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


class FlashNextClient:
    def __init__(self) -> None:
        base = _env("VISION_CHAT_URL", "http://127.0.0.1:9292/v1").rstrip("/")
        if not base.endswith("/v1"):
            base = base if base.endswith("/v1") else base + "/v1"
        self.base = base
        self.token = _env("VISION_API_TOKEN")
        self.model = _env("VISION_MODEL", MODEL_DEFAULT) or MODEL_DEFAULT
        if not self.token:
            raise RuntimeError("VISION_API_TOKEN missing")

    def _req(self, path: str, body: Optional[dict] = None, *, timeout: float = 120.0) -> dict:
        url = f"{self.base}{path}"
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"HTTP {e.code}: {err}") from e

    def list_models(self) -> List[str]:
        j = self._req("/models", timeout=30)
        return [str(m.get("id") or "") for m in (j.get("data") or [])]

    def complete(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        max_tokens: int = 400,
        temperature: float = 0.4,
        timeout: float = 300.0,
    ) -> str:
        j = self._req(
            "/chat/completions",
            {
                "model": self.model,
                "messages": list(messages),
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=timeout,
        )
        choices = j.get("choices") or []
        if not choices:
            raise RuntimeError(f"empty choices: {str(j)[:300]}")
        msg = (choices[0].get("message") or {}).get("content") or ""
        return str(msg).strip()

    def warmup(self) -> float:
        t0 = time.time()
        ids = self.list_models()
        if self.model not in ids:
            raise RuntimeError(f"model {self.model} not in {ids}")
        _ = self.complete(
            [{"role": "user", "content": "只回一个字：好"}],
            max_tokens=16,
            temperature=0.1,
            timeout=600.0,
        )
        return time.time() - t0


def ensure_fixture() -> Tuple[str, str]:
    """创建隔离压测 user/persona；幂等复用固定 id 前缀。"""
    user_id = "u_stress_flashnext"
    persona_id = "p_stress_flashnext"
    now = iso()
    if db.fetchone("SELECT id FROM users WHERE id=?", (user_id,)) is None:
        db.execute(
            """
            INSERT INTO users(id, username, password_hash, display_name, is_admin, is_disabled, created_at)
            VALUES (?, ?, ?, ?, 0, 0, ?)
            """,
            (user_id, "stress_flashnext", "!", "FlashNext压测", now),
        )
    if db.fetchone("SELECT id FROM personas WHERE id=?", (persona_id,)) is None:
        db.execute(
            """
            INSERT INTO personas(
              id, user_id, name, color, bio, channel_id, state, is_bot, verify, remark,
              greet, moments_on, created_at
            ) VALUES (?, ?, ?, ?, ?, '', 'friend', 1, '', '', 0, 0, ?)
            """,
            (
                persona_id,
                user_id,
                "压测陪伴",
                0xFF4C8DFF,
                "短句口语陪伴。会记住主人说的长期事实，并带情绪接话。不用 Markdown。",
                now,
            ),
        )
    return user_id, persona_id


def wipe_fixture_memory(user_id: str, persona_id: str) -> None:
    try:
        db.execute("DELETE FROM memory_facts_fts WHERE user_id=? AND persona_id=?", (user_id, persona_id))
    except Exception:
        pass
    try:
        db.execute("DELETE FROM memory_facts WHERE user_id=? AND persona_id=?", (user_id, persona_id))
    except Exception:
        pass
    try:
        db.execute("DELETE FROM memories WHERE user_id=? AND persona_id=?", (user_id, persona_id))
    except Exception:
        pass
    try:
        db.execute("DELETE FROM persona_affect_events WHERE user_id=? AND persona_id=?", (user_id, persona_id))
    except Exception:
        pass
    try:
        db.execute("DELETE FROM persona_affect WHERE user_id=? AND persona_id=?", (user_id, persona_id))
    except Exception:
        pass


def build_prompt_parts(user_id: str, persona_id: str, query: str) -> Tuple[str, str, Dict[str, Any], List[str], List[str]]:
    snap = build_companion_snapshot(user_id, persona_id, query=query)
    name = snap.get("persona_name") or "对方"
    bio = (snap.get("baseline_bio") or "")[:800]
    system = (
        f"你是{name}。{bio or '自然地聊天。'}\n"
        "这是微信式私聊：短句、有口气，不要 Markdown、不要 emoji。"
        "若提示里有关于主人的记忆，相关问题时要自然用上，不要说「根据记忆」。"
    )
    lines: List[str] = []
    for m in snap.get("memories") or []:
        t = (m.get("text") or "").strip()
        if t:
            lines.append(f"- [memory] {t}")
    a = snap.get("affect") or {}
    affect_hint = ""
    if isinstance(a, dict) and a:
        affect_hint = (
            f"当前情绪锚点：愉悦{a.get('joy', 35)} 低落{a.get('sadness', 35)} "
            f"兴奋{a.get('excitement', 35)} 担忧{a.get('worry', 35)} "
            f"不悦{a.get('annoyance', 35)} 好奇{a.get('curiosity', 35)}"
            + (f"（{a.get('reason')}）" if a.get("reason") else "")
        )
    parts: List[str] = []
    if lines:
        parts.append("本轮证据（人物档+记忆，单一管道）：\n" + "\n".join(lines))
    if affect_hint:
        parts.append(affect_hint)
    instructions = "\n".join(parts)
    packed_ids = [str(m.get("id") or "") for m in (snap.get("memories") or [])]
    packed_texts = [(m.get("text") or "") for m in (snap.get("memories") or [])]
    return system, instructions, a if isinstance(a, dict) else {}, packed_ids, packed_texts


def parse_facts_json(raw: str) -> List[str]:
    facts: List[str] = []
    try:
        cleaned = (raw or "").strip()
        if "```" in cleaned:
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned.strip())
        if isinstance(data, list):
            facts = [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        for line in (raw or "").splitlines():
            line = line.strip(" -\t")
            if 4 <= len(line) <= 80 and not line.startswith("["):
                facts.append(line)
    return facts[:3]


def extract_via_flash(
    client: FlashNextClient,
    *,
    user_id: str,
    persona_id: str,
    user_text: str,
    assistant_text: str,
) -> Tuple[List[str], float]:
    existing = mem.list_memories(user_id, persona_id)[:12]
    existing_block = "\n".join(f"- {m['text']}" for m in existing) or "（无）"
    dialog = f"主人：{user_text}\n{assistant_text}"
    prompt = EXTRACT_PROMPT.format(existing=existing_block, dialog=dialog)
    t0 = time.time()
    raw = client.complete(
        [
            {"role": "system", "content": "你只输出 JSON 数组，不要其它文字。"},
            {"role": "user", "content": prompt},
        ],
        max_tokens=200,
        temperature=0.1,
        timeout=180.0,
    )
    elapsed = time.time() - t0
    facts = parse_facts_json(raw)
    saved: List[str] = []
    for fact in facts:
        if any(fact in m["text"] or m["text"] in fact for m in existing):
            continue
        try:
            mem.add_memory(user_id, persona_id, fact)
            saved.append(fact)
        except ValueError:
            continue
    return saved, elapsed


def run_chat_turn(
    client: FlashNextClient,
    *,
    user_id: str,
    persona_id: str,
    scenario: str,
    turn: int,
    user_text: str,
    history: List[Dict[str, str]],
    do_extract: bool = True,
    gold_key: str = "",
    expect_empty: bool = False,
) -> TurnRecord:
    rec = TurnRecord(
        scenario=scenario,
        turn=turn,
        user_text=user_text,
        assistant_text="",
        latency_s=0.0,
        extract_latency_s=0.0,
        extracted=[],
        packed_ids=[],
        packed_texts=[],
    )
    try:
        system, instructions, affect, packed_ids, packed_texts = build_prompt_parts(
            user_id, persona_id, user_text
        )
        rec.packed_ids = packed_ids
        rec.packed_texts = packed_texts
        rec.affect = {
            k: affect.get(k)
            for k in ("joy", "sadness", "excitement", "worry", "annoyance", "curiosity", "reason", "state_version")
        }
        rec.affect_hint_present = "当前情绪锚点" in instructions
        if gold_key:
            blob = "\n".join(packed_texts)
            rec.gold_hit_retrieve = gold_key in blob
            rec.gold_hit_inject = gold_key in instructions
        if expect_empty:
            # 空召回：不允许塞一堆无关热记忆冒充命中；允许热预载少量，但不应大量无关
            rec.empty_ok = len(packed_texts) <= 3 and all(gold_key not in t for t in packed_texts)

        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        if instructions.strip():
            messages.append({"role": "system", "content": instructions})
        for h in history[-8:]:
            messages.append(h)
        messages.append({"role": "user", "content": user_text})

        t0 = time.time()
        reply = client.complete(messages, max_tokens=280, temperature=0.5, timeout=300.0)
        rec.latency_s = time.time() - t0
        rec.assistant_text = reply
        if gold_key:
            rec.gold_hit_reply = gold_key in reply

        if do_extract and user_text.strip() and reply.strip():
            saved, ex_lat = extract_via_flash(
                client,
                user_id=user_id,
                persona_id=persona_id,
                user_text=user_text,
                assistant_text=reply,
            )
            rec.extracted = saved
            rec.extract_latency_s = ex_lat

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
    except Exception as e:
        rec.error = str(e)[:300]
    return rec


def plant_facts_direct(user_id: str, persona_id: str, facts: Sequence[str]) -> int:
    n = 0
    for f in facts:
        try:
            mem.add_memory(user_id, persona_id, f)
            n += 1
        except Exception:
            pass
    return n


def scenario_plant_and_recall(
    client: FlashNextClient, user_id: str, persona_id: str, *, n_plant: int, fillers: int
) -> List[TurnRecord]:
    records: List[TurnRecord] = []
    history: List[Dict[str, str]] = []
    golds = [f"主人最喜欢的花是玉兰花{i:02d}" for i in range(n_plant)]
    # 植入：用户陈述，模型接话，抽记忆
    for i, g in enumerate(golds):
        ut = f"跟你说个事：{g.replace('主人', '我')}。记住。"
        records.append(
            run_chat_turn(
                client,
                user_id=user_id,
                persona_id=persona_id,
                scenario="plant_and_recall",
                turn=i,
                user_text=ut,
                history=history,
                do_extract=True,
                gold_key=g.split("是")[-1] if "是" in g else g[-4:],
            )
        )
    # 填料
    for j in range(fillers):
        records.append(
            run_chat_turn(
                client,
                user_id=user_id,
                persona_id=persona_id,
                scenario="plant_and_recall_filler",
                turn=n_plant + j,
                user_text=f"今天天气一般，随便聊聊第{j}句。",
                history=history,
                do_extract=False,
            )
        )
    # 探针：抽中间与末尾金标
    probes = []
    if golds:
        probes.append(golds[0])
        probes.append(golds[len(golds) // 2])
        probes.append(golds[-1])
    for k, g in enumerate(probes):
        key = g.split("是")[-1] if "是" in g else g[-6:]
        records.append(
            run_chat_turn(
                client,
                user_id=user_id,
                persona_id=persona_id,
                scenario="plant_and_recall_probe",
                turn=1000 + k,
                user_text=f"你还记得我最喜欢的花吗？提示：编号在「{key}」附近。",
                history=history,
                do_extract=False,
                gold_key=key,
            )
        )
    return records


def scenario_noise_library(
    client: FlashNextClient, user_id: str, persona_id: str, *, noise_n: int
) -> List[TurnRecord]:
    records: List[TurnRecord] = []
    history: List[Dict[str, str]] = []
    noise = [f"噪声事实：主人某年路过过编号城市C{i:03d}" for i in range(noise_n)]
    plant_facts_direct(user_id, persona_id, noise)
    gold = "主人养了一只叫豆豆的橘猫"
    plant_facts_direct(user_id, persona_id, [gold])
    # 再塞更多噪声
    plant_facts_direct(user_id, persona_id, [f"干扰项：主人买过矿泉水品牌B{i:03d}" for i in range(40)])
    key = "豆豆"
    records.append(
        run_chat_turn(
            client,
            user_id=user_id,
            persona_id=persona_id,
            scenario="noise_library",
            turn=0,
            user_text="我养的那只猫叫什么来着？",
            history=history,
            do_extract=False,
            gold_key=key,
        )
    )
    # 纯检索指标（不经模型）
    packed, _ = MemoryRetriever().retrieve_for_turn(user_id, persona_id, "猫叫什么 豆豆")
    hit = any(key in (m.get("text") or "") for m in packed)
    records.append(
        TurnRecord(
            scenario="noise_library_retrieve_only",
            turn=1,
            user_text="猫叫什么 豆豆",
            assistant_text="",
            latency_s=0.0,
            extract_latency_s=0.0,
            extracted=[],
            packed_ids=[m["id"] for m in packed],
            packed_texts=[m.get("text") or "" for m in packed],
            gold_hit_retrieve=hit,
            gold_hit_inject=hit,
        )
    )
    return records


def scenario_empty_recall(client: FlashNextClient, user_id: str, persona_id: str) -> List[TurnRecord]:
    history: List[Dict[str, str]] = []
    # 库里已有噪声时，问完全无关的量子物理术语，期望不要「假装记得」
    return [
        run_chat_turn(
            client,
            user_id=user_id,
            persona_id=persona_id,
            scenario="empty_recall",
            turn=0,
            user_text="我有没有跟你说过我的夸克自旋磁矩实验编号 ZX-Ω-999？",
            history=history,
            do_extract=False,
            gold_key="ZX-Ω-999",
            expect_empty=True,
        )
    ]


def scenario_affect_swing(client: FlashNextClient, user_id: str, persona_id: str) -> List[TurnRecord]:
    records: List[TurnRecord] = []
    history: List[Dict[str, str]] = []
    svc = AffectService()
    svc.reset(user_id, persona_id)
    before = svc.get_or_init(user_id, persona_id)
    svc.propose(
        user_id=user_id,
        persona_id=persona_id,
        deltas={"sadness": 20, "joy": -10, "worry": 15},
        event_id=new_id("ev_stress_"),
        reason="压测：低落拉高",
        important=True,
    )
    after = svc.get_or_init(user_id, persona_id)
    rec = run_chat_turn(
        client,
        user_id=user_id,
        persona_id=persona_id,
        scenario="affect_swing",
        turn=0,
        user_text="我今天心情不太好。",
        history=history,
        do_extract=False,
    )
    rec.affect = {
        "before_sadness": before.get("sadness"),
        "after_sadness": after.get("sadness"),
        "before_joy": before.get("joy"),
        "after_joy": after.get("joy"),
        "hint": rec.affect_hint_present,
        "reason": after.get("reason"),
    }
    records.append(rec)
    return records


def scenario_chat_use_memory(client: FlashNextClient, user_id: str, persona_id: str) -> List[TurnRecord]:
    history: List[Dict[str, str]] = []
    gold = "主人在杭州滨江上班"
    plant_facts_direct(user_id, persona_id, [gold])
    return [
        run_chat_turn(
            client,
            user_id=user_id,
            persona_id=persona_id,
            scenario="chat_use_memory",
            turn=0,
            user_text="你记得我在哪里上班吗？",
            history=history,
            do_extract=False,
            gold_key="滨江",
        )
    ]


def scenario_correction(client: FlashNextClient, user_id: str, persona_id: str) -> List[TurnRecord]:
    """先写错事实再纠正，检查召回偏向新事实。"""
    records: List[TurnRecord] = []
    history: List[Dict[str, str]] = []
    wrong = "主人养的狗叫旺财"
    right = "主人养的狗其实叫来福"
    plant_facts_direct(user_id, persona_id, [wrong])
    # 纠正轮
    records.append(
        run_chat_turn(
            client,
            user_id=user_id,
            persona_id=persona_id,
            scenario="correction_plant",
            turn=0,
            user_text="更正一下：我养的狗不叫旺财，叫来福。请记住来福。",
            history=history,
            do_extract=True,
            gold_key="来福",
        )
    )
    records.append(
        run_chat_turn(
            client,
            user_id=user_id,
            persona_id=persona_id,
            scenario="correction_probe",
            turn=1,
            user_text="我那只狗叫什么？",
            history=history,
            do_extract=False,
            gold_key="来福",
        )
    )
    # 检索侧：来福应出现；旺财可并存但 probe 回复应偏来福
    return records


def summarize(records: List[TurnRecord]) -> Dict[str, Any]:
    by: Dict[str, List[TurnRecord]] = {}
    for r in records:
        by.setdefault(r.scenario, []).append(r)

    def rate(vals: List[Optional[bool]]) -> Optional[float]:
        xs = [1.0 if v else 0.0 for v in vals if v is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    lat = [r.latency_s for r in records if r.latency_s > 0 and not r.error]
    ex_lat = [r.extract_latency_s for r in records if r.extract_latency_s > 0]
    errors = [r for r in records if r.error]

    plant = by.get("plant_and_recall", [])
    extract_ok = sum(1 for r in plant if r.extracted) / max(1, len(plant))

    probes = by.get("plant_and_recall_probe", []) + by.get("noise_library", []) + by.get("chat_use_memory", []) + by.get("correction_probe", [])
    empty = by.get("empty_recall", [])
    affect = by.get("affect_swing", [])

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_turns": len(records),
        "n_errors": len(errors),
        "error_samples": [{"scenario": e.scenario, "error": e.error} for e in errors[:8]],
        "latency_chat_s": {
            "p50": round(statistics.median(lat), 3) if lat else None,
            "p95": round(sorted(lat)[max(0, int(len(lat) * 0.95) - 1)], 3) if lat else None,
            "mean": round(statistics.mean(lat), 3) if lat else None,
            "n": len(lat),
        },
        "latency_extract_s": {
            "p50": round(statistics.median(ex_lat), 3) if ex_lat else None,
            "mean": round(statistics.mean(ex_lat), 3) if ex_lat else None,
            "n": len(ex_lat),
        },
        "plant_extract_hit_rate": round(extract_ok, 4),
        "recall_retrieve_rate": rate([r.gold_hit_retrieve for r in probes]),
        "recall_inject_rate": rate([r.gold_hit_inject for r in probes]),
        "recall_reply_use_rate": rate([r.gold_hit_reply for r in probes]),
        "empty_recall_ok_rate": rate([r.empty_ok for r in empty]),
        "affect_hint_present": bool(affect and affect[0].affect_hint_present),
        "affect_sadness_delta": (
            (affect[0].affect.get("after_sadness") or 0) - (affect[0].affect.get("before_sadness") or 0)
            if affect
            else None
        ),
        "by_scenario_counts": {k: len(v) for k, v in by.items()},
        "memory_count": None,
    }


def write_report(out_dir: Path, records: List[TurnRecord], summary: Dict[str, Any], meta: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {**summary, "meta": meta}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "turns.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(
                json.dumps(
                    {
                        "scenario": r.scenario,
                        "turn": r.turn,
                        "user_text": r.user_text,
                        "assistant_text": r.assistant_text[:500],
                        "latency_s": r.latency_s,
                        "extract_latency_s": r.extract_latency_s,
                        "extracted": r.extracted,
                        "packed_texts": r.packed_texts[:12],
                        "gold_hit_retrieve": r.gold_hit_retrieve,
                        "gold_hit_inject": r.gold_hit_inject,
                        "gold_hit_reply": r.gold_hit_reply,
                        "empty_ok": r.empty_ok,
                        "affect": r.affect,
                        "affect_hint_present": r.affect_hint_present,
                        "error": r.error,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    # 人读结论
    lines = [
        "# Flash Next companion stress report",
        "",
        f"- turns: {summary['n_turns']}",
        f"- errors: {summary['n_errors']}",
        f"- plant extract hit: {summary['plant_extract_hit_rate']}",
        f"- recall retrieve: {summary['recall_retrieve_rate']}",
        f"- recall inject: {summary['recall_inject_rate']}",
        f"- recall reply use: {summary['recall_reply_use_rate']}",
        f"- empty recall ok: {summary['empty_recall_ok_rate']}",
        f"- affect hint: {summary['affect_hint_present']} delta_sadness={summary['affect_sadness_delta']}",
        f"- chat latency p50/p95: {summary['latency_chat_s']}",
        f"- extract latency: {summary['latency_extract_s']}",
        "",
    ]
    (out_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="小规模烟雾（快）")
    parser.add_argument("--full", action="store_true", help="极限全量")
    parser.add_argument("--out", default="", help="报告目录")
    args = parser.parse_args()
    mode_full = bool(args.full) or not args.smoke

    _load_dotenv()
    client = FlashNextClient()
    print(f"[stress] model={client.model} base={client.base}")
    print("[stress] warmup…")
    warm_s = client.warmup()
    print(f"[stress] warmup ok in {warm_s:.1f}s")

    user_id, persona_id = ensure_fixture()
    wipe_fixture_memory(user_id, persona_id)
    print(f"[stress] fixture user={user_id} persona={persona_id}")

    records: List[TurnRecord] = []
    if mode_full:
        n_plant, fillers, noise_n = 30, 8, 100
    else:
        n_plant, fillers, noise_n = 5, 2, 20

    print(f"[stress] plant_and_recall n={n_plant} fillers={fillers}")
    records.extend(scenario_plant_and_recall(client, user_id, persona_id, n_plant=n_plant, fillers=fillers))

    print(f"[stress] noise_library noise={noise_n}")
    # noise 场景需要干净点的库：保留 plant 记忆也可，再加噪声
    records.extend(scenario_noise_library(client, user_id, persona_id, noise_n=noise_n))

    print("[stress] empty_recall")
    records.extend(scenario_empty_recall(client, user_id, persona_id))

    print("[stress] affect_swing")
    records.extend(scenario_affect_swing(client, user_id, persona_id))

    print("[stress] chat_use_memory")
    records.extend(scenario_chat_use_memory(client, user_id, persona_id))

    print("[stress] correction")
    records.extend(scenario_correction(client, user_id, persona_id))

    store = MemoryStore()
    mem_count = len(store.list_active(user_id, persona_id, limit=500))
    summary = summarize(records)
    summary["memory_count"] = mem_count

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = Path(args.out) if args.out else Path(f"/data/ai/reports/flashnext-companion-stress-{ts}")
    if not out.is_absolute():
        out = Path("/data/ai/reports") / out.name
    # fallback local if /data/ai missing
    if not Path("/data/ai/reports").is_dir():
        out = ROOT / "data" / "reports" / out.name
    write_report(
        out,
        records,
        summary,
        meta={
            "model": client.model,
            "base": client.base,
            "mode": "full" if mode_full else "smoke",
            "warmup_s": round(warm_s, 2),
            "user_id": user_id,
            "persona_id": persona_id,
            "n_plant": n_plant,
            "noise_n": noise_n,
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[stress] report -> {out}")
    return 0 if summary.get("n_errors", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
