# 运维速查

更新：2026-10-27。

## 公网穿透（手机不在局域网）

后端默认局域网。可选 `PUBLIC_TUNNEL_PROVIDER`：

| Provider | 说明 |
|----------|------|
| `none` | 局域网或自有反代 |
| `ngrok` | 免费档流式较稳 |
| `cloudflared` | 试用方便；流式优先用具名 Tunnel |
| `localhost_run` / `pinggy` | 免安装 SSH 试用 |
| `ssh_jump` | 自有 VPS 反向隧道（长期） |

```bash
cd server
python scripts/tunnel_ctl.py start ngrok   # 或 cloudflared / localhost_run
python scripts/tunnel_ctl.py status
```

`.env` 示例：`PUBLIC_TUNNEL_PROVIDER=ngrok`、`PUBLIC_TUNNEL_AUTOSTART=1`、改掉默认 `SECRET_KEY`。  
临时隧道重启后 URL 常变。DSH `:8787` 与聊天穿透分开，勿混成一条除非你有意为之。

## 备份 / 切流

1. `curl AI主机:8787/health` → ok；`DSH_GATEWAY_*` 正确。
2. `./scripts/backup_product.sh` 产出备份。
3. 推送 `dsh-product`（含 `product-companion`）、重启 gateway / uvicorn；冒烟登录聊几轮。
4. 回滚：`./scripts/restore_product.sh backups/<包>.tgz` 后重启 API。  
   DSH 坏时保持「明确失败」，**不要**重新打开本地 LLM 回退。

### schema v20 / 陪伴回滚注意

- 升级会建 `memory_facts*`、`persona_affect*`、`persona_growth*`、`companion_followups`、`companion_policy`。
- 回滚代码到 v19 前：先停写；旧二进制不认新表但一般可读旧 `memories` 双写行。
- 纠正/删除会写 `memory_source_suppress`：回滚应用后若只还原库文件，抑制与事实一致；若混用新旧包，优先整包 restore。
- TS 迁移时：**产品库仍是唯一写入归属**；DSH 插件只读快照。

### 陪伴验收报告

见 [`docs/reports/companion-baseline-phase-a.json`](./reports/companion-baseline-phase-a.json) 与  
[`docs/reports/companion-acceptance.md`](./reports/companion-acceptance.md)。

## 看 AI / 设备约定

- **当前只有虚拟机**：电脑 `docker_novnc`（XFCE），手机 `redroid` Android 13。无 USB 真机主路径；勿用 `host_x11`。
- App 打开监控 **不**自动开机；点开机才 start；关监控不 stop；关机才 stop。
- AI（brain / agent）需要操作时自启，测完自停；观看只挂 lease + live 截图。
- 多租户：每用户 ensure 实例；动作走 `vd-api.sh` + `X-Chat-User-Id` / `VD_TICKET`；人设/会话 fencing。
- 设备说明：`dsh-product/packages/product-devices/README.md`。

## 用量看板

- 管理台「用量」：上下文 / 缓存命中表格与 CSV（见 [`USAGE_METRICS.md`](./USAGE_METRICS.md)）。
- 构建：`cd server/admin-ui && npm run build` → `server/web/dist`，由 FastAPI 托管。

## 入站识图 / 文档解析

优先本机开源 **Unlimited-OCR**（Transformers HTTP，GPU1，`:9898`）；不可用时回落 Paddle/Rapid。

文档链路：`doc_parse` 先抽原生正文（`pdftotext` / docx XML / 纯文本），字数够就不再打模型；扫面 PDF/图片再交 Unlimited。

```bash
# 模型已在 /data/ai/cache/huggingface/.../Unlimited-OCR
# 服务：systemd user
systemctl --user enable --now chat-unlimited-ocr.service
journalctl --user -u chat-unlimited-ocr.service -f
# 手工：
# CUDA_VISIBLE_DEVICES=1 PYTHONPATH=/home/joy/envs/uocr-tf/lib/python3.10/site-packages:/data/ai/envs/torch-cu128/lib/python3.10/site-packages \
#   /home/joy/envs/uocr-tf/bin/python scripts/unlimited_ocr_http.py --port 9898 --preload
```

`.env`：

```bash
UNLIMITED_OCR_BASE_URL=http://127.0.0.1:9898/v1
UNLIMITED_OCR_MODEL=/data/ai/cache/huggingface/hub/models--baidu--Unlimited-OCR/snapshots/8249d20b2822a7d8a4c22c8d8c9069b5ac36bda3
UNLIMITED_OCR_TIMEOUT_SECONDS=180
OCR_BASE_URL=http://127.0.0.1:9899
```

Paddle 回落仍可用 `:9899`。

## 产品能力注册

- 本机有的能力才注册进对话（唱歌 / 一起听 / 生图 / 虚拟机等）；没有就不写暗号，对方要了如实说未开通。
- 管理台「服务器」→ 用户 →「能力」：按子用户开关（默认启用系统已有项）。
- API：`GET/PUT /api/v1/admin/users/{id}/capabilities`，用户自查 `GET /api/v1/auth/me/capabilities`。

## 公网注册 / 登录安全

- **不要用开放注册**上公网；用邀请码或关闭。管理台可改；`/api/info` 会暴露当前模式。
- 登录限速：同 IP+用户名 10 分钟 8 次；同 IP 10 分钟 40 次；失败额外延迟。
- 注册限速：同 IP 1 小时 5 次；密码至少 8 位。
- 确认 `.env` 里 `SECRET_KEY` 已换掉默认值。
