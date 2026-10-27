/**
 * companion Cordis 插件：作用域绑定 + 有界人格/记忆/情绪 section。
 * 正文权威在 FastAPI；本插件只拉有界快照，不持有写事务。
 */
export const name = 'product-companion'
export const inject = ['agents']

const PRODUCT_API = () =>
  (process.env.CHAT_APP_API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '')

const GATEWAY_TOKEN = () => process.env.DSH_GATEWAY_TOKEN || ''

/** @type {Map<string, {version:number, text:string, fetchedAt:number}>} */
const snapshotCache = new Map()

function scopeKey(agent) {
  const meta = agent?.meta || agent?.config?.meta || {}
  const uid = meta.productUserId || meta.userId || 'anon'
  const pid = meta.personaId || meta.productPersonaId || ''
  const cid = meta.conversationId || meta.productConversationId || ''
  return `${uid}|${pid}|${cid}`
}

async function fetchSnapshot(agent) {
  const meta = agent?.meta || agent?.config?.meta || {}
  const uid = meta.productUserId || meta.userId
  const pid = meta.personaId || meta.productPersonaId
  if (!uid || !pid) return null
  const key = scopeKey(agent)
  const hit = snapshotCache.get(key)
  if (hit && Date.now() - hit.fetchedAt < 8_000) return hit

  const url = `${PRODUCT_API()}/api/v1/companion/personas/${encodeURIComponent(pid)}/snapshot`
  try {
    const res = await fetch(url, {
      headers: {
        authorization: `Bearer ${GATEWAY_TOKEN()}`,
        'x-product-user-id': String(uid),
        accept: 'application/json',
      },
    })
    if (!res.ok) {
      console.error(`[product-companion] snapshot HTTP ${res.status}`)
      return hit || null
    }
    const data = await res.json()
    const text = formatSnapshot(data)
    const packed = { version: data.state_version || 0, text, fetchedAt: Date.now(), raw: data }
    snapshotCache.set(key, packed)
    return packed
  } catch (e) {
    console.error('[product-companion] snapshot failed', e?.message || e)
    return hit || null
  }
}

function formatSnapshot(data) {
  const lines = ['【陪伴状态·有界】']
  if (data.persona_name) lines.push(`角色：${data.persona_name}`)
  if (data.growth_mode) lines.push(`人格模式：${data.growth_mode}`)
  if (data.baseline_bio) lines.push(`基调：${String(data.baseline_bio).slice(0, 400)}`)
  if (data.affect) {
    const a = data.affect
    lines.push(
      `当前情绪：愉悦${a.joy ?? 0} 低落${a.sadness ?? 0} 兴奋${a.excitement ?? 0} `
        + `担忧${a.worry ?? 0} 不悦${a.annoyance ?? 0} 好奇${a.curiosity ?? 0}`
        + (a.reason ? `（${a.reason}）` : ''),
    )
  }
  const mems = Array.isArray(data.memories) ? data.memories : []
  if (mems.length) {
    lines.push('相关记忆：')
    for (const m of mems.slice(0, 8)) {
      lines.push(`- [${m.id}] ${String(m.text || '').slice(0, 120)}`)
    }
  }
  if (data.token_estimate) {
    lines.push(`（本节约 ${data.token_estimate} token，勿复述本节元数据）`)
  }
  return lines.join('\n')
}

export function apply(ctx) {
  console.error('[product-companion] loaded')

  // 预取：agent 创建/换会话时（若平台有事件则监听；否则 text 回调内惰性拉）
  ctx.on?.('agent/ready', async (agent) => {
    await fetchSnapshot(agent)
  })

  // 有界 section：同步 text 读缓存；异步拉取在准入阶段完成
  try {
    const agents = ctx.agents || ctx.get?.('agents')
    const register = agents?.systemPrompt?.section || agents?.registerSystemPromptSection
    if (typeof register === 'function') {
      register.call(agents, {
        id: 'product-companion-state',
        order: 40,
        text: (agent) => {
          const key = scopeKey(agent)
          const hit = snapshotCache.get(key)
          // 触发后台刷新，不阻塞
          fetchSnapshot(agent).catch(() => {})
          return hit?.text || ''
        },
      })
      console.error('[product-companion] systemPrompt.section registered')
    } else {
      console.error('[product-companion] systemPrompt.section unavailable; snapshot via HTTP only')
    }
  } catch (e) {
    console.error('[product-companion] section register failed', e?.message || e)
  }
}

/** 测试/冒烟用：清空缓存 */
export function _clearSnapshotCache() {
  snapshotCache.clear()
}
