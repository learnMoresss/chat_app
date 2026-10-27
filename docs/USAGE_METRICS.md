# 用量与回合指标

管理台 **用量** 页看板：近 30 天调用、上下文 Token、缓存读/写与命中率；明细表 + CSV 导出。

## 数据流

1. DSH 回合结束 → `product-gateway` `turn-metrics.js` 汇总 TokenUsage  
2. POST 产品 API 写入 `usage_logs`（schema v28+：`context_tokens` / `cache_*` / `history_messages` / `model_calls` / `metrics_json`）  
3. Web：`GET /api/v1/usage/summary`、`/logs`、`/admin/summary`、`/export.csv`  
4. App：可按会话 `GET /api/v1/usage/turns/{conv_id}`

## 字段含义

| 字段 | 含义 |
|---|---|
| `context_tokens` | 本轮上下文规模（优先 input/prompt） |
| `cache_read_tokens` | 模型缓存命中读取 |
| `cache_write_tokens` | 写入缓存 |
| `cache_hit_rate` | 缓存读 / 上下文 × 100 |
| `history_messages` | 带入历史条数（产品侧估计） |
| `model_calls` | 本轮模型调用次数 |

无 DSH TokenUsage 时可能只有估算 prompt，命中率为 0。

## 相关代码

- `dsh-product/packages/product-gateway/src/turn-metrics.js`
- `server/app/routers/usage.py`
- `server/admin-ui/src/pages/UsagePage.tsx`
