# 公网跳板媒体暂存（解决图片上传超时）

手机大图经 `8443 → SSH -R 18000 → AI:8000` 时，反向隧道偶发拥堵会超时。
改为：**手机 → 跳板本地磁盘**（快），AI 经 `SSH -L 18091` 拉取入库并删除。

## 跳板部署（需要能登录 ubuntu@119.45.30.73 的 shell 密钥）

隧道密钥仅允许 port-forward，**不能**用它 scp/ssh 执行命令。请用具备 shell 的密钥：

```bash
KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
# 同步写入 AI 主机 chat_app/server/.env：
#   MEDIA_STAGING_KEY=$KEY
#   MEDIA_STAGING_PULL_URL=http://127.0.0.1:18091
#   MEDIA_STAGING_PUBLIC_URL=https://119.45.30.73:8443/chat-staging

rsync -a ./ ubuntu@JUMP:/opt/chat-upload-staging/
# 按 chat-upload-staging.service + nginx snippet 安装
# 并把 local-services.conf 中 /chat-staging/ 段装进跳板 nginx 后 reload
```

AI 主机 `chat-public-tunnel.service` 需含：

`-L 127.0.0.1:18091:127.0.0.1:18091`

App 公网地址下会优先 `POST /chat-staging/upload` 再 `POST /api/v1/media/chat-commit`。
