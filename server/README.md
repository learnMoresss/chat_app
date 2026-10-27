# chat_app 后端

一个 Python 服务：注册/登录、人设与会话、消息、WebSocket 推送。

## 本机启动

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 可改 SECRET_KEY / SERVER_NAME
python -m app
```

默认监听 `http://0.0.0.0:8000`。

- 探活：`GET /health`
- 服务器信息：`GET /api/info`（无需登录）
- 注册：`POST /api/v1/auth/register`
- 登录：`POST /api/v1/auth/login`
- 同步：`GET /api/v1/sync`（Bearer）
- 发消息：`POST /api/v1/conversations/{id}/messages`
- WebSocket：`WS /api/v1/ws?token=<access_token>`

第一个注册的用户自动成为管理员。新用户会预置人设「深深」和一个会话。

模拟器连接：

- iOS 模拟器：`http://127.0.0.1:8000`
- Android 模拟器：`http://10.0.2.2:8000`
- 真机：填电脑局域网 IP，例如 `http://192.168.1.18:8000`
