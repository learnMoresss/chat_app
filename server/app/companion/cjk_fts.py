"""CJK bigram 文本：索引端与查询端一致，缓解 FTS5 unicode61 中文分词不足。"""
from __future__ import annotations

import re
from typing import List, Set


_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-zA-Z0-9_]{2,}")


def cjk_bigrams(text: str) -> List[str]:
    out: List[str] = []
    for run in _CJK_RUN.findall(text or ""):
        if len(run) == 1:
            out.append(run)
        else:
            for i in range(len(run) - 1):
                out.append(run[i : i + 2])
    return out


def fts_index_body(text: str) -> str:
    """写入 FTS 的 body：原文 + 空格分隔 bigram + 拉丁词。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    parts: List[str] = [raw]
    parts.extend(cjk_bigrams(raw))
    parts.extend(_LATIN.findall(raw.lower()))
    # 去重保序
    seen: Set[str] = set()
    uniq: List[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return " ".join(uniq)
