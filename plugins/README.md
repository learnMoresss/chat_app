# plugins

本仓库自管能力服务，由 `worker-agent` 按需启停。

## 协议（摘要）

每个插件目录：`plugin.yaml` + 启动入口。需提供：

- `GET /health`、`GET /manifest`
- 任务类：`POST /jobs`（可流式）+ `POST /unload`
- OpenAI 兼容类：如 ASR `/v1/audio/transcriptions`

鉴权：`Authorization: Bearer <token>`（与 agent / 网关约定）。  
目录之间只靠 HTTP，不互相 import。

| 目录 | 能力 | 状态 |
|------|------|------|
| `echo/` | 联调 | 可用 |
| `download/` | yt-dlp | 可用 |
| `video-mage/` | 视频理解 | 抽帧兜底；Mage 权重待稳 |
| `asr/` | 语音识别 | stub；**聊天主路径 ASR 走 `server` `asr_gen`，不依赖本 stub** |

**server 直连（不经本目录）**：生图 `IMAGE_GEN_*`、TTS `TTS_*`、聊天 ASR `ASR_*`、表情表 `stickers`。规划见 [`docs/ROADMAP.md`](../docs/ROADMAP.md)。
