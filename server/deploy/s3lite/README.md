# s3lite — 公网直传对象存储（开源 S3 思路的轻量实现）

解决：手机大图走 `8443→SSH隧道→chat-api` 易超时。  
改为：手机 **直传跳板磁盘**，chat-api 只拉小请求入库。

> 完整 MinIO 也可，但本机拉取官方镜像困难；s3lite 零额外依赖，用现有 Python/uvicorn 即可。

## 跳板安装（需要 shell 密钥，隧道密钥不够）

```bash
rsync -a ./ ubuntu@JUMP:/opt/s3lite/
cd /opt/s3lite
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn
# /etc/s3lite.env:
#   S3LITE_SECRET_KEY=...
#   S3LITE_DATA=/var/lib/s3lite
#   S3LITE_BUCKET=chat-media
.venv/bin/uvicorn s3lite_app:app --host 127.0.0.1 --port 9000
```

nginx（8443）增加：

```nginx
location /s3/ {
    auth_basic off;
    client_max_body_size 32m;
    proxy_pass http://127.0.0.1:9000/;
    proxy_request_buffering off;
    proxy_read_timeout 3600s;
}
```

公网基址：`https://119.45.30.73:8443/s3`

AI 主机 `.env`：

```
MEDIA_S3_PUBLIC_BASE=https://119.45.30.73:8443/s3
MEDIA_S3_ADMIN_URL=http://127.0.0.1:9000   # 经 SSH -L 或同机
MEDIA_S3_SECRET_KEY=与跳板相同
```
