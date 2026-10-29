# 幻信（仓库目录仍为 chat_app）

微信风格的 **私聊陪伴 App「幻信」**：Flutter 客户端 + FastAPI 产品后端 + **DSH 唯一思考环**。  
人设、记忆、情绪、媒体暗号在自家库里落地；重推理（生图 / TTS / ASR）可外置，聊天主路径不依赖本地 LLM 回退。

> 私有仓库。密钥与运行数据不进 Git（见下方「不会提交什么」）。

## 这是什么

面向「和一个 AI 人设长期聊」的产品原型 **幻信**，交互贴近微信：

- 文字流式气泡、表情、图、语音、位置、一起听、看 AI（虚拟电脑 / redroid）
- 人设抽卡固定音色与形象；入站 ASR / OCR；出站混合语音与唱歌链路
- 管理台：人设、记忆、陪伴看板、用量、能力开关

**不是**通用聊天 SaaS，也 **不接** QQ / 支付 / 完整群聊（群聊冻结）。

## 仓库结构

| 路径 | 说明 |
|------|------|
| `lib/` | Flutter App（iOS / Android / macOS） |
| `server/` | FastAPI + SQLite + 媒体执行 + `admin-ui/` |
| `dsh-product/` | DSH 产品配置与推送（不含 `node_modules`） |
| `plugins/`、`worker-agent/` | 能力插件协议与启停 |
| `docs/` | 规划与运维文档（见下） |

## 文档

| 文档 | 内容 |
|------|------|
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | **已实现 / 待做 / 边界**（规划以此为准） |
| [`docs/CHAT_LOGIC.md`](docs/CHAT_LOGIC.md) | 人设、情绪、记忆、媒体暗号怎么跑 |
| [`docs/OPS.md`](docs/OPS.md) | 公网穿透、备份切流、看 AI 约定 |
| [`docs/VOICE_SING.md`](docs/VOICE_SING.md) | 说话 / 唱歌实现路线 |
| [`docs/USAGE_METRICS.md`](docs/USAGE_METRICS.md) | 用量与缓存命中看板 |
| [`docs/CHAT_FUTURE.md`](docs/CHAT_FUTURE.md) | 后续可做清单 |

插件协议：`plugins/README.md`、`worker-agent/README.md`。

## 能力摘要

以 [`docs/ROADMAP.md`](docs/ROADMAP.md) 勾选为准，当前主要包括：

- **账号与聊天**：注册登录、邀请、同步 / WS、流式分句、出站整形、入站防注入
- **人设与记忆**：CRUD、记忆事实 / FTS、陪伴六维情绪、成长模式、管理台看板
- **媒体**：表情包、生图、识图 OCR、ASR、混合语音、唱歌、一起听、链接 OG 卡、位置选点
- **看 AI**：虚拟电脑 + redroid 13；监控≠开机；lease + 截图轮询
- **实时语音页**：本地实时对话 **UI**（动画 / 字幕开关 / 外放 AEC 态）；麦与 WebRTC 媒体未接
- **通话**：音视频信令骨架有；WebRTC 媒体通道未接

## 本地启动

### 后端

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填写 SECRET_KEY、DSH_GATEWAY_*，按需 IMAGE_GEN_* / TTS_* / ASR_* / TENCENT_MAP_KEY
python -m app
```

管理台改完需构建：

```bash
cd server/admin-ui && npm i && npm run build
```

重置密码：`cd server && python -m app reset-password <用户名> <新密码>`。

### 客户端

```bash
flutter pub get
flutter run
```

DSH 网关默认本机 `:8787`，需先按 `dsh-product/` 与 `docs/OPS.md` 推送并健康检查。

## 不会提交什么

`.gitignore` 已排除：

- `server/.env`、`server/data/`、各类密钥
- `**/node_modules/`、`**/.venv/`、`/build/`、`/tmp/`、`/vendor/`
- Flutter / Android / iOS 本地构建产物

克隆后自行准备 `.env` 与数据目录。

## 维护约定

- 做完功能：只改 `docs/ROADMAP.md` 勾选（必要时挪到「已实现」）
- 聊天逻辑变更：改 `docs/CHAT_LOGIC.md`
- 运维约定：改 `docs/OPS.md`
- **不要**再堆平行规划文档

## License

私有仓库，未对外授权。
