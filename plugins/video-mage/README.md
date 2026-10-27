# video-mage

Microsoft Mage-VL 视频理解插件（第二批产品化）。

当前状态：
- `main.py` 已可按需启动：**ffmpeg 抽帧兜底**（不占 GPU），返回第一人称描述骨架
- AI 主机 Mage-VL 实测：`/data/ai/apps/chat-app-mage-vl/`（需 torch≥2.6 + transformers≥5.7 + **mamba_ssm** 源码编译）
- Mage 权重实测通过后再把 `MODE` 切到 `mage_vl`；进度见 `docs/ROADMAP.md` 待做

网关 `grants` 里 `video.understand` 默认先注释，联调时再打开。
