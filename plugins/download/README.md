# download

用 yt-dlp 探测 / 下载视频。CPU 插件，由 worker-agent 按需启停。

能力：`media.download`

输入示例：

```json
{ "action": "probe", "url": "https://..." }
{ "action": "download", "url": "https://...", "format": "mp4" }
```

下载结果通过网关签发的 `upload.url` 回传（不写网关磁盘路径）。
