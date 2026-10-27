# worker-agent

算力机上的极薄代理：启停本仓库 `plugins/*`，回报 GPU，**不跑推理、不当任务中转**。

## 启动

```bash
cd worker-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export WORKER_TOKEN=dev-worker-token
export PLUGINS_ROOT=../plugins
export WORKER_HOST=127.0.0.1
export WORKER_PORT=9108
python -m agent
```

网关配置同一 `WORKER_TOKEN` 与 `http://127.0.0.1:9108`。

## 协议（摘要）

- 只认 `plugins/*/plugin.yaml` 白名单；`start` / `stop` / `unload`
- `GET /gpus`（`nvidia-smi`）；无卡返回空列表
- 鉴权：`Authorization: Bearer <WORKER_TOKEN>`
- 任务流量由网关直连插件实例地址，不经 agent 转发

插件约定见 [`plugins/README.md`](../plugins/README.md)。规划见 [`docs/ROADMAP.md`](../docs/ROADMAP.md)。
