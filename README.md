# 幻信（chat_app）

微信风格的 **私聊 AI 陪伴 App**：Flutter 客户端 + FastAPI 产品后端 + **DSH 思考环**。  
人设、记忆、情绪、媒体暗号在自家库里落地；重推理（生图 / TTS / ASR）可外置。适合自己部署、养自己的 bot。

## 快速开始

完整步骤见 **[`docs/DEPLOY.md`](docs/DEPLOY.md)**。最短路径：

```bash
# 1) 后端
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 改 SECRET_KEY、DSH_GATEWAY_*
python -m app

# 2) DSH 网关（另开终端，详见 dsh-product/README.md）
# curl http://127.0.0.1:8787/health

# 3) App
cd ..
flutter pub get && flutter run
```

第一个注册用户即为管理员；创建人设后即可当 bot 聊。

## 这是什么

面向「和一个 AI 人设长期聊」的产品原型，交互贴近微信：

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
| `docs/` | 规划、部署与运维文档 |

## 文档

| 文档 | 内容 |
|------|------|
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | **自建部署**（从零跑通） |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | 已实现 / 待做 / 边界 |
| [`docs/CHAT_LOGIC.md`](docs/CHAT_LOGIC.md) | 人设、情绪、记忆、媒体暗号 |
| [`docs/OPS.md`](docs/OPS.md) | 公网穿透、备份切流、看 AI |
| [`docs/VOICE_SING.md`](docs/VOICE_SING.md) | 说话 / 唱歌路线 |
| [`docs/USAGE_METRICS.md`](docs/USAGE_METRICS.md) | 用量与缓存命中 |

插件协议：`plugins/README.md`、`worker-agent/README.md`。

## 能力摘要

以 [`docs/ROADMAP.md`](docs/ROADMAP.md) 为准，主要包括：

- **账号与聊天**：注册登录、邀请、同步 / WS、流式分句、出站整形
- **人设与记忆**：CRUD、记忆事实 / FTS、陪伴情绪、成长模式、管理台
- **媒体**：表情包、生图、识图 OCR、ASR、混合语音、唱歌、一起听、链接卡、位置
- **看 AI**：虚拟电脑 + redroid；监控≠开机；lease + 截图轮询
- **实时语音 / 通话**：半双工语音链路可接；WebRTC 媒体通道仍在演进

## 不会提交什么

`.gitignore` 已排除：

- `server/.env`、`server/data/`、各类密钥
- `**/node_modules/`、`**/.venv/`、`/build/`、`/tmp/`
- Flutter / Android / iOS 本地构建产物

克隆后自行准备 `.env` 与数据目录。

## License

[MIT](./LICENSE)
