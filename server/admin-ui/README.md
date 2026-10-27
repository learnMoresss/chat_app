# 管理台（React + Ant Design）

Vite + React 19 + antd 6，构建到 `server/web/dist`，由 FastAPI 托管。

页面覆盖（与后端同步演进）：总览、人设 / 记忆、渠道与自检、用量看板（上下文/缓存命中）、虚拟设备、账号与注册策略、定时问候、媒体库、能力 / 任务等。勾选见 `docs/ROADMAP.md`；用量字段见 `docs/USAGE_METRICS.md`。

```bash
cd server/admin-ui
npm install
npm run build   # 输出到 ../web/dist
```

本地热更新（代理到后端 8000）：

```bash
npm run dev
```

后端需已启动：`cd server && python -m app`
