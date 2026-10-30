from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ServerInfo(BaseModel):
    name: str
    version: str
    registration_mode: Literal["open", "invite", "closed"]
    min_app_version: str
    # 公网可达地址（穿透成功后通常等于隧道 URL；无穿透时等同局域网/配置的 PUBLIC_BASE_URL）
    public_base_url: str = ""
    tunnel_provider: str = "none"
    tunnel_active: bool = False
    tunnel_public_url: str = ""


class TunnelProviderInfo(BaseModel):
    id: str
    name: str
    cost: str
    needs_account: bool
    needs_binary: Optional[str] = None
    binary_installed: bool = True
    summary: str
    setup_hint: str


class TunnelStatus(BaseModel):
    provider: str
    active: bool
    public_url: str = ""
    pid: Optional[int] = None
    error: str = ""
    updated_at: int = 0
    providers: List[TunnelProviderInfo] = Field(default_factory=list)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=64)
    invite_code: str = ""
    device_name: str = "App"


class LoginRequest(BaseModel):
    username: str
    password: str
    device_name: str = "App"


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UpdateMeRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=64)
    avatar_file_id: Optional[str] = Field(default=None, max_length=64)
    signature: Optional[str] = Field(default=None, max_length=120)
    moments_cover_file_id: Optional[str] = Field(default=None, max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: Dict[str, Any]


class PersonaOut(BaseModel):
    id: str
    name: str
    color: int
    bio: str
    channel_id: str
    state: str
    is_bot: bool
    verify: str
    remark: str
    greet: bool
    moments_on: bool
    worldline_enabled: bool = True
    avatar_file_id: str = ""
    voice_reply_pref: str = "sometimes"  # never | sometimes | always
    signature: str = ""
    memories: List[Dict[str, str]] = []


class PersonaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    bio: str = ""
    color: int = 0xFF2F6BFF
    channel_id: str = ""
    is_bot: bool = True
    greet: bool = False
    moments_on: bool = False
    worldline_enabled: bool = True
    avatar_file_id: str = ""
    verify: str = Field(default="", max_length=120)
    signature: str = Field(default="", max_length=40)


class ConversationOut(BaseModel):
    id: str
    persona_id: Optional[str] = None
    group_id: Optional[str] = None
    preview: str
    last_time: str
    unread: int
    pinned: bool
    muted: bool
    draft: str
    last_seq: int
    bg_name: str = "默认"
    bg_file_id: str = ""
    hidden: bool = False


class MessageOut(BaseModel):
    id: str
    client_id: str
    seq: int
    conv_id: str
    sender_id: str
    mine: bool
    kind: str
    text: str
    quote_id: Optional[str] = None
    seconds: int = 0
    bytes: int = 0
    state: str
    created_at: str


class SendMessageRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=64)
    text: str = Field(default="", max_length=8000)
    kind: str = "text"
    quote_id: Optional[str] = None
    seconds: int = 0
    bytes: int = 0
    # 图片/视频/文件：先走 /media/chat-upload，再带 file_id 列表发消息
    media_ids: List[str] = Field(default_factory=list)


class SendMessageResponse(BaseModel):
    message: MessageOut
    replies: List[MessageOut] = []


class ChannelOut(BaseModel):
    id: str
    name: str
    model: str
    base_url: str
    key_filled: bool
    shared: bool


class SyncResponse(BaseModel):
    personas: List[PersonaOut]
    conversations: List[ConversationOut]
    messages: List[MessageOut]
    channels: List[ChannelOut] = []
    me: Dict[str, Any]


class PersonaPatch(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    channel_id: Optional[str] = None
    remark: Optional[str] = None
    greet: Optional[bool] = None
    moments_on: Optional[bool] = None
    worldline_enabled: Optional[bool] = None
    color: Optional[int] = None
    avatar_file_id: Optional[str] = None
    voice_reply_pref: Optional[str] = None  # never | sometimes | always
    signature: Optional[str] = Field(default=None, max_length=40)
