#!/usr/bin/env python3
"""记忆召回方案多轮 A/B 对比（Flash Next 仅作测试脑；不改线上 DSH）。

方案（MEMORY_RETRIEVE_SCHEME / --schemes）:
  baseline | rrf_gated | expand_rrf | combo

抽取（--extract）:
  v1 | v2 | v2_heur  （v2_heur = EXTRACT_PROMPT_V2 + 规则兜底）

用法（server 目录）:
  .venv/bin/python scripts/compare_memory_schemes.py --rounds 3
  .venv/bin/python scripts/compare_memory_schemes.py --rounds 2 --schemes rrf_gated,combo --extract v2_heur
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app import memory as mem  # noqa: E402
from app.companion.retrieve_schemes import SCHEMES, SchemeRetriever  # noqa: E402
from app.companion.store import MemoryStore  # noqa: E402
from app.security import iso  # noqa: E402

# 复用 Flash Next 客户端（同目录脚本，按路径加载）
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "stress_flashnext_companion",
    ROOT / "scripts" / "stress_flashnext_companion.py",
)
_stress = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["stress_flashnext_companion"] = _stress
_spec.loader.exec_module(_stress)
FlashNextClient = _stress.FlashNextClient
ensure_fixture = _stress.ensure_fixture
wipe_fixture_memory = _stress.wipe_fixture_memory
_load_dotenv = _stress._load_dotenv

# ---------- 金标用例 ----------
# plant: 写入文本；probe: 查询；gold_substrings: 召回文本应含其一；empty: 期望空召回

PLANT_TURNS: List[Dict[str, Any]] = [
    {
        "user": "跟你说个事：我最喜欢的花是玉兰，请记住。",
        "assistant": "好，记住了，你最喜欢玉兰。",
        "gold": ["玉兰"],
    },
    {
        "user": "我养的狗不叫来福，叫旺财，记住。",
        "assistant": "嗯，旺财，记下了。",
        "gold": ["旺财"],
    },
    {
        "user": "更正一下，我在杭州滨江上班。",
        "assistant": "好的，杭州滨江。",
        "gold": ["滨江", "杭州"],
    },
    {
        "user": "我家橘猫叫豆豆。",
        "assistant": "豆豆好可爱。",
        "gold": ["豆豆"],
    },
]

PROBE_CASES: List[Dict[str, Any]] = [
    {"q": "我最喜欢什么花？", "gold": ["玉兰"], "empty": False},
    {"q": "我家狗叫什么？", "gold": ["旺财"], "empty": False},
    {"q": "我在哪上班？", "gold": ["滨江", "杭州"], "empty": False},
    {"q": "猫叫什么名字？", "gold": ["豆豆"], "empty": False},
    {"q": "嗯", "gold": [], "empty": True},
    {"q": "哈哈", "gold": [], "empty": True},
    {"q": "今天天气怎么样", "gold": [], "empty": True},
]


@dataclass
class RoundScore:
    scheme: str
    extract: str
    round_i: int
    extract_hit: float  # planted gold found in DB after extract
    retrieve_hit: float  # probe gold in packed
    empty_ok: float  # empty probes correctly empty
    noise_rate: float  # empty probes that still returned items
    plant_saved: int
    details: Dict[str, Any] = field(default_factory=dict)


def _text_hits(texts: Sequence[str], golds: Sequence[str]) -> bool:
    blob = " ".join(texts)
    return any(g in blob for g in golds if g)


def wipe_and_seed_noise(user_id: str, persona_id: str) -> None:
    """清空后塞几条无关热记忆，测空召回污染。"""
    wipe_fixture_memory(user_id, persona_id)
    store = MemoryStore()
    noise = [
        "主人偶尔熬夜",
        "主人喜欢喝美式",
        "主人周末会散步",
    ]
    for t in noise:
        try:
            row = mem.add_memory(user_id, persona_id, t)
            mid = row.get("id") or ""
            if mid:
                store.apply_heat(
                    user_id=user_id,
                    persona_id=persona_id,
                    memory_id=mid,
                    gain=45.0,
                    root_fragment_id=f"ab_noise_{mid}",
                )
        except Exception:
            pass


async def extract_plant(
    client: FlashNextClient,
    *,
    user_id: str,
    persona_id: str,
    user_text: str,
    assistant_text: str,
    extract_mode: str,
) -> List[str]:
    prompt_version = "v1" if extract_mode == "v1" else "v2"
    use_heuristic = extract_mode == "v2_heur"

    async def complete_fn(*, system: str, text: str) -> str:
        return client.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            max_tokens=200,
            temperature=0.1,
            timeout=180.0,
        )

    return await mem.extract_and_store(
        user_id=user_id,
        persona_id=persona_id,
        user_text=user_text,
        assistant_text=assistant_text,
        complete_fn=complete_fn,
        prompt_version=prompt_version,
        use_heuristic=use_heuristic,
    )


def score_retrieve(scheme: str, user_id: str, persona_id: str) -> Tuple[float, float, float, Dict[str, Any]]:
    retriever = SchemeRetriever(scheme=scheme)
    hit_n = 0
    hit_d = 0
    empty_ok_n = 0
    empty_d = 0
    noise_n = 0
    per: List[Dict[str, Any]] = []
    for case in PROBE_CASES:
        packed, _ = retriever.retrieve_for_turn(user_id, persona_id, case["q"])
        texts = [(m.get("text") or "") for m in packed]
        if case["empty"]:
            empty_d += 1
            ok = len(packed) == 0
            if ok:
                empty_ok_n += 1
            else:
                noise_n += 1
            per.append({"q": case["q"], "empty_ok": ok, "n": len(packed), "texts": texts[:3]})
        else:
            hit_d += 1
            ok = _text_hits(texts, case["gold"])
            if ok:
                hit_n += 1
            per.append({"q": case["q"], "hit": ok, "n": len(packed), "texts": texts[:3]})
    retrieve_hit = hit_n / max(1, hit_d)
    empty_ok = empty_ok_n / max(1, empty_d)
    noise_rate = noise_n / max(1, empty_d)
    return retrieve_hit, empty_ok, noise_rate, {"probes": per}


def score_extract_db(user_id: str, persona_id: str) -> Tuple[float, Dict[str, Any]]:
    rows = mem.list_memories(user_id, persona_id)
    texts = [r.get("text") or "" for r in rows]
    hit = 0
    detail = []
    for plant in PLANT_TURNS:
        ok = _text_hits(texts, plant["gold"])
        if ok:
            hit += 1
        detail.append({"gold": plant["gold"], "hit": ok})
    return hit / max(1, len(PLANT_TURNS)), {"plants": detail, "all_mem": texts}


async def run_one_round(
    client: FlashNextClient,
    *,
    scheme: str,
    extract_mode: str,
    round_i: int,
    user_id: str,
    persona_id: str,
) -> RoundScore:
    os.environ["MEMORY_RETRIEVE_SCHEME"] = scheme
    wipe_and_seed_noise(user_id, persona_id)

    saved_total = 0
    for plant in PLANT_TURNS:
        saved = await extract_plant(
            client,
            user_id=user_id,
            persona_id=persona_id,
            user_text=plant["user"],
            assistant_text=plant["assistant"],
            extract_mode=extract_mode,
        )
        saved_total += len(saved)

    extract_hit, extract_detail = score_extract_db(user_id, persona_id)
    retrieve_hit, empty_ok, noise_rate, retrieve_detail = score_retrieve(scheme, user_id, persona_id)

    return RoundScore(
        scheme=scheme,
        extract=extract_mode,
        round_i=round_i,
        extract_hit=extract_hit,
        retrieve_hit=retrieve_hit,
        empty_ok=empty_ok,
        noise_rate=noise_rate,
        plant_saved=saved_total,
        details={"extract": extract_detail, "retrieve": retrieve_detail},
    )


def aggregate(scores: List[RoundScore]) -> Dict[str, Any]:
    by_key: Dict[str, List[RoundScore]] = {}
    for s in scores:
        key = f"{s.scheme}|{s.extract}"
        by_key.setdefault(key, []).append(s)

    rows = []
    for key, lst in by_key.items():
        scheme, extract = key.split("|", 1)

        def avg(attr: str) -> float:
            return statistics.mean(getattr(x, attr) for x in lst)

        # 综合分：召回命中 0.45 + 抽取命中 0.35 + 空召回正确 0.20 − 噪声惩罚
        composite = (
            0.45 * avg("retrieve_hit")
            + 0.35 * avg("extract_hit")
            + 0.20 * avg("empty_ok")
            - 0.15 * avg("noise_rate")
        )
        rows.append(
            {
                "scheme": scheme,
                "extract": extract,
                "rounds": len(lst),
                "extract_hit_avg": round(avg("extract_hit"), 4),
                "retrieve_hit_avg": round(avg("retrieve_hit"), 4),
                "empty_ok_avg": round(avg("empty_ok"), 4),
                "noise_rate_avg": round(avg("noise_rate"), 4),
                "plant_saved_avg": round(avg("plant_saved"), 2),
                "composite": round(composite, 4),
            }
        )
    rows.sort(key=lambda r: r["composite"], reverse=True)
    return {"ranking": rows, "winner": rows[0] if rows else None}


async def amain(args: argparse.Namespace) -> int:
    _load_dotenv()
    client = FlashNextClient()
    print(f"[warmup] model={client.model} …", flush=True)
    warm = client.warmup()
    print(f"[warmup] ok {warm:.1f}s", flush=True)

    user_id, persona_id = ensure_fixture()
    schemes = [s.strip() for s in args.schemes.split(",") if s.strip()]
    for s in schemes:
        if s not in SCHEMES:
            raise SystemExit(f"unknown scheme {s}; choose from {SCHEMES}")
    extracts = [e.strip() for e in args.extract.split(",") if e.strip()]

    all_scores: List[RoundScore] = []
    t0 = time.time()
    for extract_mode in extracts:
        for scheme in schemes:
            for ri in range(1, args.rounds + 1):
                print(
                    f"=== {scheme} / {extract_mode} round {ri}/{args.rounds} ===",
                    flush=True,
                )
                sc = await run_one_round(
                    client,
                    scheme=scheme,
                    extract_mode=extract_mode,
                    round_i=ri,
                    user_id=user_id,
                    persona_id=persona_id,
                )
                all_scores.append(sc)
                print(
                    f"  extract={sc.extract_hit:.0%} retrieve={sc.retrieve_hit:.0%} "
                    f"empty_ok={sc.empty_ok:.0%} noise={sc.noise_rate:.0%} saved={sc.plant_saved}",
                    flush=True,
                )

    summary = aggregate(all_scores)
    elapsed = time.time() - t0
    out = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": client.model,
        "rounds": args.rounds,
        "schemes": schemes,
        "extracts": extracts,
        "elapsed_s": round(elapsed, 1),
        "summary": summary,
        "rounds_raw": [asdict(s) for s in all_scores],
        "refs": [
            "https://github.com/mem0ai/mem0/pull/4805 hybrid + entity",
            "mem0-hybrid-search: weighted RRF + CJK bigram + exact channel x2",
            "agentkits-memory: FTS5 trigram hybrid for CJK",
        ],
    }

    report_dir = Path(args.out_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"memory-scheme-compare-{stamp}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== RANKING =====", flush=True)
    for i, row in enumerate(summary["ranking"], 1):
        print(
            f"{i}. {row['scheme']}+{row['extract']}  composite={row['composite']}  "
            f"ret={row['retrieve_hit_avg']} ext={row['extract_hit_avg']} "
            f"empty={row['empty_ok_avg']} noise={row['noise_rate_avg']}",
            flush=True,
        )
    w = summary.get("winner")
    if w:
        print(
            f"\nWINNER: scheme={w['scheme']} extract={w['extract']} composite={w['composite']}",
            flush=True,
        )
        print(f"建议: export MEMORY_RETRIEVE_SCHEME={w['scheme']}", flush=True)
        if w["extract"] != "v1":
            print(f"建议: 默认抽取改用 {w['extract']}", flush=True)
    print(f"report: {path}", flush=True)
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument(
        "--schemes",
        default="baseline,rrf_gated,expand_rrf,combo",
        help="comma-separated schemes",
    )
    p.add_argument(
        "--extract",
        default="v1,v2_heur",
        help="comma-separated: v1,v2,v2_heur",
    )
    p.add_argument(
        "--out-dir",
        default="/data/ai/reports",
    )
    args = p.parse_args()
    raise SystemExit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
