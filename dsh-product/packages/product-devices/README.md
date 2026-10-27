# product-devices（P3）

虚拟电脑 / 虚拟手机设备面。**不跑第二套 Agent**：思考仍在 DSH；权限与租约在产品网关（FastAPI）。

## 当前能力（以虚拟机为准）

| 设备 | 后端 | 说明 |
|---|---|---|
| 虚拟电脑 | `docker_novnc`（XFCE） | 截图/点击/输入 + 窗口树 `ui-dump`/`ui-tap` |
| 虚拟手机 | `redroid` Android 13 | adb + uiautomator 控件树；观看走 API live 轮询截图 |

**没有 USB 真机主路径**（代码里可保留 register-usb 扩展位，产品默认当虚拟机用）。  
**不要**用 `host_x11`（会控 AI 主机真桌面）。

## 产品网关 API（权威）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/virtual-devices/ensure` | 保证当前用户有桌面/手机实例（可按人设认领） |
| POST | `…/{desktop\|phone}/start` `…/stop` | 启停；**停机不删记录** |
| POST | `…/leases` | `role=view\|control`；人设/会话 fencing |
| POST | `…/actions/ui-dump\|ui-find\|ui-tap` | 控件/窗口树优先 |
| POST | `…/actions/screenshot\|click\|type\|key` | 截图与坐标兜底 |
| GET | `…/{id}/live/vnc.html` | 多实例可寻址观看（截图轮询，走 chat-api 隧道） |

DSH 侧：每轮写 `product-vd/env.sh` + `VD_TICKET`，用 `scripts/vd-api.sh` 调 API（防漏 Header）。

## 门槛

| 门槛 | 状态 |
|---|---|
| 浏览器可见操作（view_url → live） | ✅ |
| 接管 fencing（user/persona/conv） | ✅ |
| 停启不误删 | ✅ |
| DSH 动作与观看同机 | docker exec / adb→redroid |
| 每用户独立实例端口 | ✅ 动态分配 |
| USB 真机 | ❌ 非当前目标 |
