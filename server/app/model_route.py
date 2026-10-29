"""人设渠道 → DSH provider/model。

默认官方 DeepSeek（deepseek-official）。日卡已过期，请求日卡一律映射到官方。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

DAYCARD_PROVIDER = "daycard-deepseek"
DAYCARD_MODEL = "deepseek-v4.1-flash"
LOCAL_QWEN_PROVIDER = "local-qwen"
LOCAL_QWEN_MODEL = "qwen3.8-flash-next-iq3_xxs"
OFFICIAL_PROVIDER = "deepseek-official"
OFFICIAL_MODEL = "deepseek-chat"
OFFICIAL_MODELS = frozenset({"deepseek-flash", "deepseek-chat", "deepseek-reasoner"})

KNOWN_PROVIDERS = frozenset({DAYCARD_PROVIDER, LOCAL_QWEN_PROVIDER, OFFICIAL_PROVIDER})


def is_official_route(provider: str, model: str) -> bool:
    p = (provider or "").strip()
    m = (model or "").strip()
    if p == OFFICIAL_PROVIDER:
        return True
    if m in OFFICIAL_MODELS:
        return True
    if m.startswith("deepseek-") and not m.startswith("deepseek-v4"):
        if "v4" not in m and "8888" not in m:
            return m in OFFICIAL_MODELS or m.endswith("-flash") and "v4" not in m
    return False


def parse_model_spec(spec: str) -> Optional[Tuple[str, str]]:
    """解析 `provider/model` 形式。"""
    s = (spec or "").strip()
    if not s or "/" not in s:
        return None
    left, right = s.split("/", 1)
    left, right = left.strip(), right.strip()
    if left in KNOWN_PROVIDERS and right:
        return left, right
    return None


def _coerce_away_daycard(provider: str, model: str) -> Tuple[str, str]:
    """日卡过期：任何 daycard / 8888ok / v4 日卡模型 → 官方 chat。"""
    p = (provider or "").strip()
    m = (model or "").strip()
    if p == DAYCARD_PROVIDER:
        return OFFICIAL_PROVIDER, OFFICIAL_MODEL
    if m.startswith("deepseek-v4") or m.startswith("daycard-"):
        return OFFICIAL_PROVIDER, OFFICIAL_MODEL
    return p, m


def resolve_dsh_route(
    channel: Optional[Mapping[str, Any]] = None,
    *,
    override: str = "",
) -> Tuple[str, str]:
    """返回 (provider, model)。默认官方；日卡强制改官方。"""
    parsed = parse_model_spec(override)
    if parsed:
        return _coerce_away_daycard(*parsed)

    ch = dict(channel or {})
    model = str(ch.get("model") or "").strip()
    base = str(ch.get("base_url") or "").strip().lower()
    name = str(ch.get("name") or "").strip()

    parsed = parse_model_spec(model)
    if parsed:
        return _coerce_away_daycard(*parsed)

    # 旧日卡渠道配置：直接官方
    if "8888ok" in base or "日卡" in name or model.startswith("deepseek-v4"):
        return OFFICIAL_PROVIDER, OFFICIAL_MODEL

    if (
        ":9292" in base
        or "local-qwen" in model.lower()
        or model.lower().startswith("qwen")
        or "本地" in name
    ):
        m = model
        if m.lower().startswith("local-qwen/"):
            m = m.split("/", 1)[1]
        return LOCAL_QWEN_PROVIDER, (m or LOCAL_QWEN_MODEL)

    if "api.deepseek.com" in base or model in OFFICIAL_MODELS:
        return OFFICIAL_PROVIDER, (model or OFFICIAL_MODEL)

    # 缺省：官方
    return OFFICIAL_PROVIDER, OFFICIAL_MODEL


def usage_model_label(provider: str, model: str) -> str:
    p, m = _coerce_away_daycard(
        (provider or "").strip() or OFFICIAL_PROVIDER,
        (model or "").strip() or OFFICIAL_MODEL,
    )
    return f"{p}/{m}"


def assert_not_official_for_stress(provider: str, model: str, *, label: str = "") -> None:
    """压测守卫（历史接口保留）：现已默认官方，此函数不再拦截官方。"""
    return
