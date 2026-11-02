# 自建部署指南

目标：在你自己的机器上跑起「幻信」——Flutter App + FastAPI 后端 + DSH 思考环，做成可登录聊天的 AI 陪伴 bot。

最低可跑通：**本机后端 + 本机 DSH + 模拟器/真机 App**。生图 / TTS / ASR / 看 AI 可后接。

## 0. 环境要求

| 组件 | 建议 |
|------|------|
| 操作系统 | macOS / Linux（Windows 可用 WSL2） |
| Python | 3.11+ |
| Node.js | 20+（DSH / 管理台） |
| Flutter | 稳定版（客户端） |
| GPU | 可选；语音 / 生图 / OCR 需要时再配 |

## 1. 克隆与后端

```bash
git clone https://github.com/learnMoresss/chat_app.git
cd chat_app/server
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`（必改）：

```bash
SECRET_KEY=请换成足够长的随机串
SERVER_NAME=我的幻信
PUBLIC_BASE_URL=http://127.0.0.1:8000
DSH_GATEWAY_ENABLED=1
DSH_GATEWAY_URL=http://127.0.0.1:8787
DSH_GATEWAY_TOKEN=与 DSH 网关一致的令牌
```

启动 API：

```bash
python -m app
# 探活: curl http://127.0.0.1:8000/health
```

管理台（可选）：

```bash
cd admin-ui && npm i && npm run build
# 产物进 server/web/dist，由 FastAPI 托管
```

第一个注册的用户自动成为管理员。

## 2. DSH 思考环（bot 大脑）

聊天主路径走 DSH，没有本地 LLM 回退。最小本机方式：

```bash
cd dsh-product
npm i
# 按 dsh-product/README.md 配置 profile / gateway.env
# 令牌写入 .dsh-home/gateway.env，与 server/.env 的 DSH_GATEWAY_TOKEN 一致
```

确认：

```bash
curl -s http://127.0.0.1:8787/health
```

生产可用 systemd user 单元（见仓库内 `server/deploy/systemd/` 样例，若有）或自行托管 `product-gateway`。细节见 [`dsh-product/README.md`](../dsh-product/README.md) 与 [`OPS.md`](./OPS.md)。

## 3. Flutter 客户端

```bash
cd chat_app   # 仓库根
flutter pub get
flutter run
```

连后端：

- iOS 模拟器：`http://127.0.0.1:8000`
- Android 模拟器：`http://10.0.2.2:8000`
- 真机：填电脑局域网 IP，例如 `http://192.168.1.18:8000`

在 App 注册 → 创建人设 → 开聊。人设即你的 bot；记忆 / 情绪 / 媒体能力随后端配置逐步打开。

## 4. 可选能力

| 能力 | 配置入口 |
|------|----------|
| 公网访问（手机在外网） | `.env` → `PUBLIC_TUNNEL_PROVIDER=cloudflared` / `ngrok` 等，见 [`OPS.md`](./OPS.md) |
| TTS / ASR | `TTS_*`、`ASR_*`、`TTS_COSYVOICE_URL` 等 |
| 生图 | `IMAGE_GEN_*` / gateway |
| 位置卡 | `TENCENT_MAP_KEY` |
| 看 AI（虚拟电脑 / redroid） | `DESKTOP_BACKEND=docker_novnc` 等，见 OPS |

没配这些时，文字聊天与人设 bot 仍可工作；对应媒体卡会降级或不可用。

## 5. 备份

```bash
cd server
./scripts/backup_product.sh    # 若脚本存在
# 至少备份 DATA_DIR（默认 ./data）与 .env
```

## 6. 安全清单

- [ ] 改掉默认 `SECRET_KEY` / `DSH_GATEWAY_TOKEN`
- [ ] 不要把 `.env`、`server/data/` 提交进 Git
- [ ] 公网务必 HTTPS；临时隧道 URL 会变
- [ ] 管理台与邀请码策略按你的场景收紧

## 相关文档

- 产品边界：[`ROADMAP.md`](./ROADMAP.md)
- 聊天逻辑：[`CHAT_LOGIC.md`](./CHAT_LOGIC.md)
- 运维穿透：[`OPS.md`](./OPS.md)
- 说话 / 唱歌：[`VOICE_SING.md`](./VOICE_SING.md)
