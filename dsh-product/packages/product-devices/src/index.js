/**
 * product-devices：隔离虚拟电脑/手机能力说明（当前只有虚拟机，无 USB 真机主路径）。
 * 优先用 vd-api（自动带隔离 Header / VD_TICKET）。
 */
export const name = 'product-devices'
export const inject = ['agents']

const API = (process.env.PRODUCT_GATEWAY_URL || process.env.CHAT_API_BASE || 'http://127.0.0.1:8000').replace(
  /\/$/,
  '',
)
const ROOT = (process.env.PRODUCT_ROOT || '').replace(/\/$/, '') || '/home/joy/chat_app/dsh-product'
const VD = `${ROOT}/scripts/vd-api.sh`
const ENV_SH = `${process.env.DSH_HOME || '.'}/product-vd/env.sh`

function vd(method, path, body = '') {
  const src = `set -a; [ -f "${ENV_SH}" ] && . "${ENV_SH}"; set +a`
  if (body) {
    return `${src}; bash "${VD}" ${method} ${path} '${body}'`
  }
  return `${src}; bash "${VD}" ${method} ${path}`
}

export function apply(ctx) {
  const hint = [
    '【设备能力·隔离】当前只有虚拟机：虚拟电脑=docker XFCE/noVNC；虚拟手机=redroid（Android 13）。不是主机真桌面，也不是用户 USB 真机。',
    `硬性：先 source ${ENV_SH}，再用 vd-api；禁止漏 Header、禁止用别人的 userId。`,
    '硬性顺序：①人设口吻先回一句；②短进度；③停/关机 → stop?force=1。',
    `保证设备: ${vd('POST', 'ensure', '{"kind":"desktop"}')} 或 kind=android`,
    `开机桌面: ${vd('POST', 'desktop/start')}`,
    `开机手机: ${vd('POST', 'phone/start')}`,
    `桌面窗口树(优先): ${vd('POST', 'desktop/actions/ui-dump')}`,
    `桌面按窗点击: ${vd('POST', 'desktop/actions/ui-tap', '{"text":"Terminal"}')} 或 {"ref":"@w1"}`,
    `桌面截图兜底: ${vd('POST', 'desktop/actions/screenshot')}`,
    `手机控件树(优先): ${vd('POST', 'phone/actions/ui-dump')}`,
    `手机找/点: ${vd('POST', 'phone/actions/ui-find', '{"text":"设置"}')} / ui-tap`,
    `关机: ${vd('POST', 'desktop/stop?force=1')}（手机同理 phone/stop）`,
    '桌面/手机都优先 ui-dump→ui-tap；无节点再截图。长任务走 agent 轨。',
    `API_BASE=${API}`,
  ].join('\n')

  ctx.logger?.info?.('[product-devices] VM-only (docker desktop + redroid) ready')

  try {
    ctx.agents?.defaults?.()?.prompt?.append?.(hint)
  } catch {
    /* ignore */
  }

  ctx.provide?.('product.devices', {
    apiBase: `${API}/api/v1/virtual-devices`,
    vdApi: VD,
    envSh: ENV_SH,
    hint,
  })
}
