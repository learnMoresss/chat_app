"""人设渠道 → DSH provider/model。

默认日卡（daycard-deepseek），避免误烧官方 DEEPSEEK_API_KEY。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

DAYCARD_PROVIDER = "daycard-deepseek"
DAYCARD_MODEL = "deepseek-v4.1-flash"
LOCAL_QWEN_PROVIDER = "local-qwen"
LOCAL_QWEN_MODEL = "qwen3.8-flash-next-iq3_xxs"
OFFICIAL_PROVIDER = "deepseek-official"
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
        # deepseek-flash / deepseek-chat 等官方 id
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


def resolve_dsh_route(
    channel: Optional[Mapping[str, Any]] = None,
    *,
    override: str = "",
) -> Tuple[str, str]:
    """返回 (provider, model)。

    override 优先（形如 daycard-deepseek/deepseek-v4.1-flash）。
    """
    parsed = parse_model_spec(override)
    if parsed:
        return parsed

    ch = dict(channel or {})
    model = str(ch.get("model") or "").strip()
    base = str(ch.get("base_url") or "").strip().lower()
    name = str(ch.get("name") or "").strip()

    parsed = parse_model_spec(model)
    if parsed:
        return parsed

    if "8888ok" in base or "日卡" in name or model.startswith("deepseek-v4"):
        return DAYCARD_PROVIDER, (model or DAYCARD_MODEL)

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
        return OFFICIAL_PROVIDER, (model or "deepseek-flash")

    # 缺省：日卡，禁止默认落到官方 key
    return DAYCARD_PROVIDER, DAYCARD_MODEL


def usage_model_label(provider: str, model: str) -> str:
    p = (provider or "").strip() or DAYCARD_PROVIDER
    m = (model or "").strip() or DAYCARD_MODEL
    return f"{p}/{m}"


def assert_not_official_for_stress(provider: str, model: str, *, label: str = "") -> None:
    """压测守卫：一旦落到官方模型立刻中止。"""
    if is_official_route(provider, model):
        raise RuntimeError(
            f"stress_abort_official_key: got {provider}/{model}"
            + (f" ({label})" if label else "")
            + " — 禁止使用官方 DeepSeek key，请改用日卡"
        )
