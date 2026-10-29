/**
 * 组装进入 DSH 的提示：新会话带 system+历史；续聊只推本轮（会话内已有历史）。
 */

const CHAT_STYLE = (
  '这是微信式私聊，不是问答机器人。1～3 句口语，可反问接话；'
  + '不要助手腔，不要 emoji/颜文字，不要 Markdown，不要说自己是 AI。'
)

function deviceHint(userId, personaId, convId) {
  const uid = userId || '$CHAT_USER_ID'
  const pid = personaId || ''
  const cid = convId || ''
  const envSh = '$DSH_HOME/product-vd/env.sh'
  return (
    '【虚拟设备·多租户隔离】当前只有虚拟机（docker 桌面 + redroid 手机），不是主机物理桌面，也不是 USB 真机。'
    + `当前用户=${uid}`
    + (pid ? ` 人设=${pid}` : '')
    + (cid ? ` 会话=${cid}` : '')
    + `。每轮先 source ${envSh}，再用 $PRODUCT_ROOT/scripts/vd-api.sh（自动带隔离 Header）。禁止裸 curl 漏 Header、禁止用别人的 userId。`
    + '硬性顺序：①人设口吻先回一句；②短进度；③停/关机 → vd-api POST desktop/stop?force=1。'
    + '桌面/手机都优先 ui-dump→ui-tap（桌面是窗口树 @wN，手机是控件 @eN）；截图仅兜底。'
    + '不同用户/人设/会话不得抢控制权；长任务走 agent 轨。'
  )
}

const DEVICE_HINT = deviceHint('', '', '')

const STICKER_HINT = (
  '【表情】单张配图写 ⟦表情：图上文字或场景短描述⟧；要「一组/一包」时先问用户主题，'
  + '确认后再写 ⟦表情组：主题⟧（或 agent 轨 POST /api/v1/stickers/packs/web_collect，notify=1+conv_id）。'
  + '禁止把一组需求只丢一张单图应付。实景写 ⟦图：一句话⟧。禁止 Unicode emoji。'
  + '系统按标签检索/收组，不要罗列库。'
)

/**
 * @param {{
 *   isNew: boolean,
 *   system?: string,
 *   history?: Array<{role:string, content:string}>,
 *   text: string,
 *   instructions?: string,
 * }} opts
 */
export function buildTurnPrompt(opts) {
  const text = String(opts.text || '').trim()
  const mode = String(opts.mode || 'chat')
  const userId = String(opts.userId || '')
  const personaId = String(opts.personaId || '')
  const conversationId = String(opts.conversationId || '')
  const agentLead =
    mode === 'agent'
      ? '【本轮是设备 agent 轨】先回一句再操作；进度可短句输出；可被用户打断。\n'
        + 'source "$DSH_HOME/product-vd/env.sh" 2>/dev/null; '
        + '用 bash "$PRODUCT_ROOT/scripts/vd-api.sh" 调设备 API。\n'
      : ''
  const scopedDevice = deviceHint(userId, personaId, conversationId)
  if (!opts.isNew) {
    // 续聊：轻量记忆补丁（若有）+ 用户话；agent 轨补隔离提示
    const patch = String(opts.instructions || '').trim()
    const deviceLine = mode === 'agent' ? `${scopedDevice}\n\n` : ''
    if (patch) return `${agentLead}${deviceLine}【补充设定/记忆】\n${patch}\n\n【用户说】\n${text}`
    return `${agentLead}${deviceLine}${text}`
  }

  const system = String(opts.system || opts.instructions || '').trim() || CHAT_STYLE
  const history = Array.isArray(opts.history) ? opts.history : []
  const lines = [
    '【系统】',
    system.includes(CHAT_STYLE) ? system : `${system}\n${CHAT_STYLE}`,
    scopedDevice || DEVICE_HINT,
    STICKER_HINT,
    '',
  ]
  for (const m of history.slice(-20)) {
    const role = m.role === 'assistant' ? '你' : '用户'
    const content = String(m.content || '').trim()
    if (!content) continue
    lines.push(`【${role}】${content}`)
  }
  lines.push('', '【用户】', text)
  return lines.join('\n')
}
