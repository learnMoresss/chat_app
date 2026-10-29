# ROADMAP（以代码为准）

更新：2026-10-28。只保留 **已实现 / 待做 / 边界**。  
聊天怎么跑（人设、情绪、记忆、媒体暗号）→ [`CHAT_LOGIC.md`](./CHAT_LOGIC.md)  
陪伴世界线改造（事件/记忆底座/意图/上下文；含与现状偏差纠正）→ [`COMPANION_WORLDLINE.md`](./COMPANION_WORLDLINE.md)  
运维（公网、备份切流）→ [`OPS.md`](./OPS.md)  
用量看板 → [`USAGE_METRICS.md`](./USAGE_METRICS.md)

做完一件：在「待做」勾掉，必要时挪进「已实现」一行；**详细方案只维护 COMPANION_WORLDLINE，不在此复制长文**。

---

## 边界（不变）

- 一个 Flutter App + FastAPI 产品 API + React 管理台；**DSH（`:8787`）唯一思考环**，无本地 LLM 回退。
- 账号 / 人设 / 记忆 / 聊天 / 文件归属由我们库表维护；DSH session 日志不能替代产品数据。
- **不接 QQ / NapCat** 等；可借鉴 qq-bridge 的机制（会话不静默换、草稿闸门、唤醒缓冲），不借 QQ 传输。
- 群聊冻结；无支付钱包；设备面只有虚拟机（docker 桌面 + redroid），不控主机真桌面 / 不做 USB 真机主路径。
- 重推理（生图 / TTS / ASR / 视频理解）可外置进程；聊天主路径用配置 URL 直连，与 `plugins/*` 生命周期线并行。
- 媒体：**DSH 只出文本暗号** → chat_app 本地执行（不是 DSH 插件注入）。见 CHAT_LOGIC。

---

## 已实现

### 产品与聊天

- [x] 注册登录、邀请、多用户隔离；sync / WS / 流式气泡 / 分句 / 出站整形 / 入站防注入
- [x] 人设 CRUD、头像上传、记忆增删改；回合后抽事实 + 关键词检索；人物档 / 证据注入
- [x] 思考环走 `dsh_gateway`（闲聊 / 记忆抽取 / 调度文案）
- [x] 表情包表 + API；角色 `⟦表情⟧` + 情绪启发式；用户图不进人民设库；约 90s 冷却
- [x] schema **v30**：`sticker_packs`（global/account）+ `pack_id`；多图 collect；`suggest`；加入气泡
- [x] 入站识图：百度 Unlimited-OCR 优先（本机 Paddle/Rapid 回落）；文档先抽正文再 OCR
- [x] `⟦图⟧` → Z-Image / Qwen，失败氛围卡
- [x] 入站语音 ASR（`asr_gen`：transcriptions URL 或本机 whisper）进模型上下文
- [x] 出站混合语音：固定音色时 `⟦语音⟧` → **Breeze**（+情绪语气）；冷却 / 字数闸（路线见 `VOICE_SING.md`）
- [x] 音色·形象抽卡 API + App 页；固定写 `voice_profiles` / `avatar_file_id`（唱歌参考音同源）
- [x] App：相册多选发图、语音录放、表情收藏真实 `media_ids`、用量页
- [x] 主动问候 / 朋友圈调度初版；管理台初版（人设、记忆、渠道、任务等）
- [x] schema **v20**（`memory_facts` / FTS / 抑制 / 温度 / `persona_affect` / 成长修订 / followups / policy）
- [x] 陪伴：DSH `product-companion` 插件 + 有界快照 API；单一检索管道（禁「未命中塞最近」）；16k 软预算
- [x] 六维情绪锚点 + 成长三模式（adaptive / initial_locked / snapshot_locked）+ 结构化 followup
- [x] 管理台「陪伴看板」（ECharts）+ App WebView 同源 `board.html`

### 看 AI / 设备

- [x] 虚拟电脑（docker XFCE）+ redroid 13；App 小窗/全屏监控
- [x] 打开监控不唤醒；互斥开机/关机；关监控不 stop
- [x] 全屏顶栏深色底白字/白图标
- [x] 多租户 ensure / lease fencing / `vd-api`+`VD_TICKET`；桌面 wmctrl + 手机 uiautomator
- [x] live 观看走 chat-api 截图轮询（可寻址，不依赖单 novnc 端口）

### 用量

- [x] DSH turn-metrics → `usage_logs`（上下文 / 缓存读写下命中）
- [x] 管理台用量看板表格 + CSV；`/usage/turns/{conv_id}`

### 能力注册与安全

- [x] 产品能力注册表（系统有才注册；对话提示按注册注入；未开通如实告知）
- [x] 管理台按子用户开关能力（默认启用系统已有项）
- [x] 登录/注册限速 + 公网默认邀请码注册

### 网关插件（部分）

- [x] `worker-agent` + `plugins/echo`、`download` 可跑
- [~] `plugins/asr`、`video-mage`：协议/stub 或抽帧兜底（聊天主路径不依赖 stub）

---

## 待做（按优先级）

### P0 体验缺口

- [x] 语音气泡下可折叠 ASR 字幕（转写入 meta.asr +「转文字」）
- [x] DSH/模型失败 → 红色系统友好提示（不露技术文案）
- [x] 多图新版叠卡片可滑动（非九宫格）+ 鉴权图重试
- [x] 链接 OG 分享卡（纯 URL）；管理台朋友圈列表
- [x] 人设「语音回复偏好」UI（从不/偶尔/总是）+ 服务端闸门
- [x] App 列表高度/键盘互斥等微信细节（按痛点做）

### P1 媒体与能力

- [x] 唱歌语音条：`⟦唱：曲名⟧` → cover/compose + Seed-VC soft；音色跟人设固定样音（验收 `tmp/sing-fanqiehua-xueer.mp3`，`VOICE_SING.md`）
- [x] 曲库：`music_tracks` / 歌单表 + NAS 双根扫描 API；带伴奏混回；compose 质量门
- [x] 开发模拟推送：`/api/v1/dev/sim-voice`（说话/唱歌样例进 App）
- [x] `⟦视频⟧` 接线（生图+ffmpeg 短片；失败降级图/文案）
- [x] 图生图（人设头像 ref）+ 生图日配额（`IMAGE_DAILY_LIMIT`）
- [x] `plugins/asr`、`plugins/tts` 真模型包装（可与现直连并存）
- [x] 图片/视频入站描述进上下文（`vision_gen`；无 vision URL 时启发式）
- [x] 链接卡片解析（OG）；更多菜单任务卡仍保留
- [x] 网络歌单 `POST /music/playlists/{id}/sync`（m3u/歌名匹配曲库）
- [x] 唱歌：命中曲库源曲时默认 `with_backing`
- [x] 一起听：曲目可带 `lyrics` 字段（schema v25）

### P2 记忆 / 世界线 / 运维

- [x] 记忆分类型 / 来源纠正删除 / FTS 检索 / 纠错抑制屏障（schema v20）
- [x] 检索默认 `rrf_gated`（空召回热记忆门控）；抽取 v2+启发式（机制压测后）
- [x] sqlite-vec 可选（`scripts/memory_nightly.py --vec`；未装库则跳过）
- [x] 暂停记忆读写（`companion_policy`）+ 看板操作
- [x] 课程记忆体检 / 夜间流水线（`scripts/memory_nightly.py`）
- [x] 备份回滚演练脚本（`scripts/backup_drill.sh [--restore-dry]`）
- [x] 管理台「世界线」Tab：Neural Field 白主题**前端模拟**（未接后端状态）
- [x] 世界线阶段 0 基线：见 [`reports/worldline-phase0-baseline.md`](./reports/worldline-phase0-baseline.md)（单进程；插件双重注入关；忙只改措辞）
- [x] 世界线阶段 1：raw_records、显式记忆前置、抽取 background_jobs、FTS bigram、去 500 扫、ContextCompiler
- [x] 世界线阶段 2：companion_events、action_intents、worldline_enabled、Runtime/DshAdapter
- [x] 世界线阶段 3：WorldlineService（日计划/模拟天气/扰动）+ 忙时措辞注入（不拖延）
- [x] 世界线阶段 4：ActionDispatcher；退役 moment_daily；发帖不升格记忆
- [x] 世界线阶段 5：语义通道开关位、可选真实天气、看板 worldline/intents、成长证据修订、默认关 memories 双写
- [x] 世界线 Tab 监控看板：接 board/worldline/turn-trace，只读展示主路径过程（不从此页发消息）
- [ ] 陪伴世界线长跑与生产 DSH 记忆评测（运维侧）
  - 世界线按人设可开关（默认开）；关闭 = 仅对话驱动
  - 观察模式：`WORLDLINE_ACTIONS=0`；打开行动默认 `1`
  - 真实天气：`WORLD_WEATHER_REAL=1`；语义：`MEMORY_SEMANTIC=1`（需 sqlite-vec）
- [ ] FastAPI→TS 产品包迁移（按模块搬；**写入归属仍在产品库**；**不恢复双主脑**）— 仍属长期项

### P3 设备与观测（仍缺）

- [ ] 桌面 AT-SPI / 更细控件语义（当前 wmctrl 窗口树为主）
- [ ] DSH 侧 typed tool schema（少靠长 prompt 拼 curl）
- [ ] 真 noVNC/WS 多实例直连（现 live 截图轮询可够用）
- [ ] App 内回合级用量 UI（API 已有 `/usage/turns`）
- [ ] 设备看板：按用户/人设占用与租约可视化增强

### 明确不做（近期）

- 红包、转账、支付、名片、拍一拍
- USB 真机主路径、控主机真桌面、接 QQ 通道、把宿主机其它项目源码嵌进 monorepo
- 完整群聊会话模型（仍冻结）

### 聊天扩展（近况）

- [x] 位置：腾讯逆地理 / 周边（缓存限流）+ 离线区片名兜底；选点页高德底图；卡片展示地名非裸经纬度
- [x] 位置按需「⟦附近⟧」；额度满时聊天侧可走 DSH 联网总结（不刷地图配额）
- [x] 消息长按：微信式深色图标气泡（贴气泡定位），去掉底部白面板 / 模拟失败项
- [x] 一起听：去重 + 曲库选歌弹层；链接入口已去掉（防崩溃）
- [x] 实时语音对话页 UI（`realtime_voice.dart`）：球体动画、字幕开关、静音/外放/挂断
- [x] 实时语音半双工接线：SenseVoice ASR→DSH 分句→CosyVoice 流式 PCM（`voice_audio`）→App 播放；启动预热 +「连接中」门闩；公网 `/chat-api`
- [ ] 实时语音：更强原生 Voice Processing AEC；流式 ASR
- [ ] 语音/视频通话：WebRTC 媒体通道（信令骨架已有）

### 后续可做（见 `CHAT_FUTURE.md`）

- [x] 位置消息、合并转发（卡片骨架 + API kind）
- 群聊（仍冻结）
- [x] 一起听歌骨架 + 歌词字段；跨端强同步 / 听流特征仍可增强
- [x] 语音/视频通话信令骨架（`/api/v1/calls`；WebRTC 媒体未接）

---

## 目录速查

| 路径 | 作用 |
|------|------|
| `server/` | 产品 API、库、媒体执行、管理台构建产物 |
| `lib/` | Flutter App |
| `dsh-product/` | DSH 产品配置 / 推送脚本 |
| `plugins/`、`worker-agent/` | 能力插件协议与启停（说明在各自 README） |
| `docs/CHAT_LOGIC.md` | 聊天逻辑权威说明 |
| `docs/VOICE_SING.md` | 说话/唱歌确定实现路线 |
| `docs/OPS.md` | 公网穿透、备份切流、设备约定 |
| `docs/USAGE_METRICS.md` | 用量 / 缓存命中看板 |

数据：`server/data/chat.db` + `server/data/files/<user_id>/`。
