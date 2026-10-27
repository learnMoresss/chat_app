# 说话 / 唱歌：确定实现路线（本期锁定）

更新：2026-10-26。以代码为准；改路线先改本文再改实现。

---

## 总原则

| | 说话 | 唱歌 |
|--|------|------|
| 暗号 | `⟦语音：短句⟧` | `⟦唱：曲名⟧`（可选 `清唱｜` / `带伴奏｜`） |
| 音色从哪来 | 人设**已固定**的抽卡音色描述（`persona_assets.prompt`） | 人设**已固定**语音样音（`voice_profiles` / 固定 asset 的 wav） |
| 引擎 | **Breeze TTS 2**（失败可回退 VoiceDesign） | **Seed-VC soft 基线**（AI 主机 `sing-pipeline`） |
| 情绪 | **要**：叠当前 `persona_affect` 语气指令 | 不跟六维说话情绪；靠歌声本身 |
| 气泡 | `kind=voice` | `kind=voice` + `meta.kind_extra=sing` |
| 偏好 | `personas.voice_reply_pref`：never / sometimes / always（只约束说话） | 点名唱仍允许 |
| 明确不用 | IndexTTS 做日常说话（平、假） | 按歌手手工切时间轴；双人混轨直接 SVC |

DSH 只出暗号文本；chat_app 抠暗号后本地/SSH 执行。见 `CHAT_LOGIC.md`。

模拟推送（开发）：`POST /api/v1/dev/sim-voice` 或 `server/scripts/sim_voice_push.py`。

---

## 说话（Breeze）

1. 闸门：必须有固定音色；`never` 并回文字；`sometimes` 冷却约 45s；`always` 跳过冷却；标记内字数上限约 80。
2. `instruction` = **固定音色文案** + **情绪语气**（`media_cards.speak_instructions`）。
3. 调用：`tts_gen.synthesize_voice_wav` → 默认 `TTS_ENGINE=breeze`。
4. 落盘 wav → 语音气泡。

验收样例：`tmp/sing-xueer-speak.mp3`。

---

## 唱歌（Seed-VC soft）

1. 闸门：`SING_ENABLED`；冷却约 120s。
2. 曲名解析：歌单名 → `music_tracks` 标题 → 远程 `SONG_ALIASES` → 否则 compose（过门禁才交付）。
3. 参考音：优先本机固定样音；否则 `SING_DEFAULT_PERSONA_REF`。
4. 换声：pitch+3、steps≈45、cfg≈0.65；人声 deharsh。
5. `with_backing`：分轨 instrumental + 柔化人声混回（伴奏不走 deharsh）。
6. compose 门禁：时长≥4s 且 RMS≥0.02，否则失败文案。

验收样例：`tmp/sing-fanqiehua-xueer.mp3`。

---

## 曲库

| 概念 | 说明 |
|------|------|
| 曲目 `music_tracks` | **账号级全局池**（`user_id`，与人设无关）；`source=nas\|cache`；扫飞牛「音乐」「新音乐」双根 + 本地 `data/music_cache` |
| 歌单 `music_playlists` | `kind=library` 手建 / `session` 一起听临时抽 / `network` 网络歌单（登记 `source_url`，同步另期） |
| 一起听会话 | `listen_sessions` 只记心情、进度、挂哪个临时歌单；**不**每人设一份曲库 |
| API | `POST /scan`、`GET /library`、`GET /tracks?q&limit&offset`、`/playlists?kind=` |
| 后台 | 运维 → **曲库**：列表 / 搜索 / 扫描 |
| NAS | `MUSIC_NAS_*`；飞牛 SMB `192.168.1.9`，共享名即 **「音乐」「新音乐」**；本机不可达时经 AI 主机 `smbprotocol` 扫描；密码在 `.env` 的 `MUSIC_NAS_PASSWORD` |

---

## 一起听（已接曲库）

- AI：`⟦一起听：心情⟧` → `music_curate` 从账号曲库抽 → `kind=session` 歌单 → `listen_sessions` + WS `listen_invite`
- App：共听页 + 顶栏接受 + 系统正在播放（audio_service）
- 流：`GET /api/v1/music/tracks/{id}/audio`（本地 / NAS 经 cache）
- 后续：网络歌单进池或外链播放；所有人设仍共用同一曲库

## 另期

通话、`⟦视频⟧`、图配额、图片入站描述。
