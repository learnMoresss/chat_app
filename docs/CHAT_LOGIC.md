# 聊天逻辑

大白话。代码主路径：`server/app/routers/chat.py`。规划勾选见 [`ROADMAP.md`](./ROADMAP.md)。

## 不是 DSH 插件注入

```text
你说话 → chat_app → DSH 只「想」出带暗号的文字
                ↓
           chat_app 抠暗号 → 本地生图 / TTS / 表情库 → 落库推气泡
```

| 角色 | 做什么 |
|------|--------|
| DSH `:8787` | 唯一思考环，出纯文本（可含 `⟦图⟧` / `⟦表情⟧` / `⟦语音⟧` / `⟦唱⟧`） |
| chat_app | 组上下文、抠暗号、调本机服务、写库（事实权威） |
| `product-companion` | DSH 薄插件：拉有界快照 section，不持有写事务 |
| 抽卡 API | App 直调 `persona_assets`，**不经 DSH** |
| `plugins/*` | 任务/worker 线；与聊天气泡暗号不是同一注入路径 |

入站语音：ASR 转写后「[对方语音] 正文」再喂 DSH。

---

## 人设怎么回（单一装配）

每轮：

1. FastAPI 用 `companion` 快照（人格 baseline + 当前情绪 + 预算内记忆）拼 **instructions**。
2. `build_messages` 的 system **不再重复塞记忆块**（`include_memory_in_system=False`）。
3. 近文默认最多约 6 轮；总输入软上限约 16k（`ContextBudgeter`）。
4. DSH companion 插件可再挂有界 section（同源 snapshot API）。

旧「system 记忆 + evidence 双通道」已收敛为同一检索管道。空召回合法，**禁止**未命中塞最近几条。

成长模式 `initial_locked` 时回复用 baseline bio，不被旧聊天改人格。

---

## 情绪

1. **持久六维**（愉悦/低落/兴奋/担忧/不悦/好奇）在 `persona_affect`：锚点 + 半衰期衰减。
2. 模型提案经 `POST .../affect/propose` 校验幅度/版本/幂等后提交；**禁止**「自称开心」直接写库。
3. 回复路径读已提交状态进提示；旧关键词贴纸启发式仍可补表情，**不叠**持久情绪。
4. 看板只读，不唤醒设备、不触发反思。

---

## 记忆

权威表 `memory_facts`（schema v20）；旧 `memories` 行迁移为 `legacy_unverified` 并双写兼容。

| 能力 | 做法 |
|------|------|
| 类型 | fact / preference / event / lesson / inference |
| 检索 | FTS5 + 中文词面；空召回合法 |
| 纠正 | CORRECT → 旧 superseded + source suppress；下一请求无旧值 |
| 温度 | 合格加热事件幂等；检索/注入/看板**不**加热 |
| 抽取 | 回合后短 DSH session 提案 → `MemoryStore` 提交 |

`person_notes` 仍可与记忆同进证据块（同一管道）。

---

## 成长

`persona_growth`：模式 `adaptive` | `initial_locked` | `snapshot_locked`。  
可塑维度起步：外向 / 玩笑 / 探索 / 主动；证据门槛 ≥3 独立根；日变粗限。  
回滚产生新 epoch；看板可切模式。

---

## 主动跟进

`companion_followups`：来源、窗口、取消条件、幂等键。调度 tick 过期清理。  
设备任务失败不写成功记忆，并可按 `cancel_when` 取消跟进。

---

## 媒体暗号

| 暗号 | 执行 | 备注 |
|------|------|------|
| `⟦表情：标签⟧` | sticker / 生图 | ~90s 冷却 |
| `⟦图：画面⟧` | Z-Image / Qwen / 氛围卡 | App 多图为叠卡片可滑，非九宫格 |
| `⟦语音：短句⟧` | **Breeze** + 固定音色 + 情绪语气 | 需抽卡固定；偏好 never/sometimes/always；入站 ASR → `meta.asr` |
| `⟦唱：曲名⟧` | Seed-VC soft + 曲库解析 / 带伴奏混回 | `kind_extra=sing`；~120s；见 `VOICE_SING.md` |
| `⟦视频：…⟧` | （丢弃） | 待做；用户视频消息可发播 |

说话/唱歌锁定路线与验收样例 → [`VOICE_SING.md`](./VOICE_SING.md)。

思考环失败：写入 `kind=system` + `level=warn` 友好文案，**不**把 DSH/堆栈塞进气泡。  
后续设想见 [`CHAT_FUTURE.md`](./CHAT_FUTURE.md)。

数据：`data/chat.db`；文件 `data/files/<user_id>/`。

关键文件：`companion/*`、`llm.py`、`memory.py`、`evidence.py`、`media_cards.py`、`tts_gen.py`、`sing_gen.py`、`stickers.py`、`routers/companion_api.py`、`routers/chat.py`、`dsh-product/packages/product-companion`。
