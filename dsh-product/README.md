# dsh-product

产品侧 DSH profile / Cordis 插件。**DSH 本体不 fork**。

## 默认部署：AI 主机系统直装

服务器已验证：`~/opt/node-v24.11.0-linux-x64` + `~/chat_app/dsh-product`（`@deepseek-ai/dsh@0.2.1-rc.3`）。  
本机只写 `packages/` / `profiles/`，用 `scripts/push-to-server.sh` 同步。`docker/` 仅备援，日常不用。

```bash
# 本机
./scripts/push-to-server.sh

# 服务器
export PATH=~/opt/node-v24.11.0-linux-x64/bin:$PATH
cd ~/chat_app/dsh-product
export DSH_HOME=$PWD/.dsh-home PRODUCT_ROOT=$PWD
node scripts/p0-smoke.mjs run && node scripts/p0-smoke.mjs resume
```

## 锁定版本

锁定：commit `22ddee379240` · npm `@deepseek-ai/dsh@0.2.1-rc.3`。规划见 `../docs/ROADMAP.md`。

## 模块

| 包 | 职责 |
|---|---|
| `product-foundation` | DB、会话映射 |
| `product-gateway` | 登录 / HTTP·WS；经 SDK 驱动同机 DSH |
| `product-chat` | 流式投影进产品消息 |
| 其余 `product-*` | P1+ |

## 与旧 FastAPI 联调

本机 `server/.env`：

```
DSH_GATEWAY_ENABLED=1
DSH_GATEWAY_URL=http://127.0.0.1:8787
DSH_GATEWAY_TOKEN=…   # 与服务器 .dsh-home/gateway.env 一致
```

隧道：`./scripts/ensure-dsh-tunnel.sh`（公网 ssh-config → 8787）。
