from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = Path("./data")
    secret_key: str = "dev-only-change-me"
    server_name: str = "未读"
    public_base_url: str = "http://127.0.0.1:8000"
    min_app_version: str = "1.0.0"
    access_token_minutes: int = 60
    refresh_token_days: int = 30
    registration_mode: str = "invite"  # open | invite | closed（公网默认邀请码）
    # 能力网关：worker-agent 与授权配置（YAML）；空则尝试 ./gateway.yaml
    gateway_config: str = ""
    # 生图：auto | zimage | qwen；URL 默认走本机 SSH 隧道
    image_gen_backend: str = "auto"
    image_gen_comfy_url: str = "http://127.0.0.1:8188"
    image_gen_qwen_url: str = "http://127.0.0.1:8091"
    image_gen_timeout_seconds: float = 180.0
    # 音色抽卡默认：真实 Breeze TTS 2（github.com/breezeblue-ai/breeze-tts）
    tts_speech_url: str = "http://127.0.0.1:8883"
    tts_engine: str = "breeze"  # breeze | voicedesign
    # Qwen3-TTS VoiceDesign（旧后端，可作回退）
    tts_voicedesign_url: str = "http://127.0.0.1:8882"
    # 固定音色 CustomVoice（按需启动，默认 8881）
    tts_customvoice_url: str = "http://127.0.0.1:8881"
    # Breeze TTS 2 流式 API（raw PCM 24kHz s16le）
    tts_breeze_url: str = "http://127.0.0.1:8883"
    # IndexTTS-2.5 / Fun-CosyVoice3 按需网关
    tts_indextts_url: str = "http://127.0.0.1:8884"
    tts_cosyvoice_url: str = "http://127.0.0.1:8885"
    tts_timeout_seconds: float = 300.0
    tts_default_sample_text: str = "你好呀，我是你的专属陪伴，今天想听我说点什么？"
    # 入站 ASR：OpenAI 兼容 transcriptions；空则走本机 whisper 子进程
    asr_transcriptions_url: str = ""
    asr_api_token: str = ""
    asr_model: str = "whisper-1"
    asr_timeout_seconds: float = 120.0
    asr_whisper_python: str = ""
    asr_whisper_model: str = "medium"
    # 入站识图：本机 PaddleOCR（文字+坐标）；空则仅启发式。不用 VL。
    ocr_base_url: str = "http://127.0.0.1:9899"
    ocr_timeout_seconds: float = 30.0
    # 旧 VL 配置保留字段以免 .env 报错，代码不再使用
    vision_chat_url: str = ""
    vision_api_token: str = ""
    vision_model: str = ""
    vision_timeout_seconds: float = 90.0
    # 出站语音：同会话冷却（秒）、标记内最长字数
    voice_reply_cooldown_seconds: float = 45.0
    voice_reply_max_chars: int = 80
    # 唱歌语音条：SSH 调 AI 主机 sing-pipeline（Seed-VC soft 基线）
    sing_enabled: bool = True
    sing_ssh_config: str = "/Users/blacktom06/Desktop/code/公网访问-119.45.30.73/ssh-config"
    sing_ssh_host: str = "local-ai-public"
    sing_default_persona_ref: str = "/data/ai/tmp/sing-demo-vevo/xueer_ref_concat.wav"
    sing_timeout_seconds: float = 600.0
    sing_cooldown_seconds: float = 120.0
    sing_prompt_max_chars: int = 200
    # 曲库：飞牛 NAS 外置根 + 本地缓存
    music_nas_smb_host: str = "192.168.1.9"
    music_nas_user: str = "syl"
    music_nas_password: str = ""
    music_nas_roots: str = "音乐,新音乐"
    music_nas_mount: str = ""  # 空则 ~/mnt/fnos-music
    music_cache_dir: str = ""  # 空则 data_dir/music_cache
    music_audio_exts: str = "mp3,flac,m4a,wav,aac,ogg"
    # 开发模拟推送：非空则允许 Header X-Dev-Token 调用 /api/v1/dev/*
    dev_sim_token: str = ""
    # DSH 主脑（唯一思考环，无本地 LLM 回退）。必须启用并配置 URL/TOKEN。
    dsh_gateway_enabled: bool = True
    dsh_gateway_url: str = "http://127.0.0.1:8787"
    dsh_gateway_token: str = ""
    dsh_gateway_timeout_seconds: float = 180.0
    # 公网穿透（无自有 VPS 时选免费方案；有跳板用 ssh_jump）
    # none | cloudflared | ngrok | localhost_run | pinggy | ssh_jump
    public_tunnel_provider: str = "none"
    public_tunnel_autostart: bool = False
    public_tunnel_ngrok_authtoken: str = ""
    public_tunnel_cloudflared_token: str = ""
    public_tunnel_ssh_config: str = ""
    public_tunnel_ssh_host: str = ""
    public_tunnel_ssh_remote_port: int = 0
    # P3 虚拟电脑：mock | aio_sandbox | docker_novnc（隔离容器；勿用 host_x11 控真机）
    desktop_backend: str = "mock"
    desktop_aio_base_url: str = ""
    desktop_aio_token: str = ""
    desktop_docker_image: str = "consol/ubuntu-xfce-vnc"
    # 容器内 noVNC HTTP 端口：consol=6901，dorowu=80，linuxserver/webtop=3000；0=按镜像推断
    desktop_docker_internal_port: int = 0
    # 容器内 DISPLAY，默认 :1
    desktop_docker_display: str = ":1"
    # 16083：避开主机 websockify 占用的 16081
    desktop_novnc_host_port: int = 16083
    desktop_public_path: str = "/vd/desktop/"
    desktop_resolution: str = "1280x800"
    android_docker_image: str = "redroid/redroid:13.0.0-latest"
    android_novnc_host_port: int = 16082
    android_public_path: str = "/vd/android/"
    # 腾讯位置 WebService（逆地理/周边）；空则 place 能力不可用
    tencent_map_key: str = ""



@lru_cache
def get_settings() -> Settings:
    return Settings()
