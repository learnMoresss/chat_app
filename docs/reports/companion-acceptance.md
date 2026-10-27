# 陪伴记忆 / 情绪 / 成长 — 验收报告

生成于实施会话；区分：已完成 / 测过 / 待配置 / 已知限制。

## 已完成

| 阶段 | 内容 |
|------|------|
| A | `dsh-product/packages/product-companion` + `companion.product.patch.yml`；基线脚本 `scripts/companion-baseline-report.mjs` |
| B | schema v20；`MemoryStore` / `Retriever` / `ContextBudgeter`；纠错抑制；砍「最近几条兜底」；chat 单一装配 |
| C | `persona_affect` + 管理台 ECharts 看板 + App WebView `board.html` |
| D | 成长三模式、证据门槛、回滚、epoch |
| E | `companion_followups`、看板操作、policy 暂停、seq/state_version 游标字段 |
| F | ROADMAP / CHAT_LOGIC / OPS 更新 |

## 测过（本机脚本）

- 迁移至 schema 20、事实 CRUD / correct 抑制、空召回、预算 pack、A→B→A 纠正链、情绪幂等、成长证据门槛、followup 幂等。
- **1k 条记忆**：注入仍 ≤8 条 / ~60 token（不随库线性涨）。
- Phase A 基线 JSON：续聊 prompt 远短于新会话全量历史；单一装配已落地。
- FastAPI 注册 companion 路由 21 条；admin-ui `tsc -b` 通过。

## 待配置

- 真实 DSH 连续 20–50 轮：需网关 `:8787` + `DSH_GATEWAY_TOKEN` 与在线模型。
- 可选 sqlite-vec / embedding：未启用（可关位保留）。
- 管理台需 `admin-ui` 重新 `npm run build` 后静态资源才含新页。

## 已知限制

- FTS 依赖本机 SQLite FTS5；失败时回退词面重叠（仍不兜底最近条）。
- companion 插件 `systemPrompt.section` 随 DSH 版本可能不可用 → 仍可靠 FastAPI instructions。
- 成长「跨会话证据」目前按 evidence_ids 计数，未做独立会话图分析。
- App WebView 需能访问 API 主机与 CDN ECharts。

## 核心验收句

角色能延续；记忆有来源可纠可删；成长可关可回滚；库涨上下文仍有界。
