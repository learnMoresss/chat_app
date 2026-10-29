# 世界线阶段 0 基线（2026-10-28）

## 部署形态

- `chat-api`：`uvicorn app.main:app --host 0.0.0.0 --port 8000`，**单进程**（父进程 `sg docker` 拉起）。
- 结论：人设级并发用 **asyncio 锁**即可；暂不需要多 worker 库内租约。

## DSH conversationId 历史

- 产品侧 `dsh_turn(_stream)` 显式传 `history`（[`dsh_gateway.py`](../../server/app/dsh_gateway.py)）。
- 抽取已用独立 `sys-mem-*` conversationId。
- **未在本阶段对 gateway 做黑盒双盲实测**（避免扰动生产会话）。策略：
  - 用途隔离继续强制不同 conversationId（`chat` / `mem` / `plan` / …）。
  - 若后续发现 DSH 自带历史膨胀：按 token/条数轮换 chat conversationId，并从权威状态重建。
  - 以 FastAPI 显式 `history` 为权威输入。

## 插件二次注入

- FastAPI `_generate_reply` 经 `build_companion_snapshot` 把记忆/情绪写入 `instructions`。
- DSH `product-companion` 插件另拉 `/snapshot` 写入 `systemPrompt.section` `product-companion-state`。
- **结论：存在双重注入风险。** 阶段 1+ 默认关闭插件 section 正文注入（`PRODUCT_COMPANION_INJECT=0`），由产品侧 ContextCompiler 唯一组装；插件仅保留加载与可选诊断。

## 检索 500 上限

- [`retrieve_schemes.py`](../../server/app/companion/retrieve_schemes.py) `_overlap_list`：`list_active(..., limit=500)`
- [`retriever.py`](../../server/app/companion/retriever.py) `lexical` 回退路径同样 500
- FTS 索引端未写 CJK bigram（仅查询端 `_tokens` 扩展）

## 语音终稿

- `voice_realtime`：ASR 结果推 WS + `_append_voice_turn` 入会话；无独立 `raw_records` 表。
- 缺口：阶段 1 用 `raw_records` 归档语音终稿与聊天原文。

## 生产 DSH 记忆评测

- 此前 Flash Next 压测数字**不能**代表生产 DSH。
- 本阶段不跑长压测（避免占用生产配额）；阶段 1 落地后用 DSH 重跑写入/召回脚本记基线。
- 已知机制问题仍成立：显式纠正未前置、词面 500、无语义通道。

## 决策回写

| 项 | 决策 |
|----|------|
| 锁 | 单进程 asyncio 人设锁 |
| 双重注入 | 关插件正文注入，产品侧唯一快照 |
| DSH 历史 | 用途隔离 + 显式 history；必要时轮换 conversationId |
| 忙时回复 | 只改措辞，不拖延 |
