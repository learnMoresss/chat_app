/**
 * 把 companion board / worldline / turn-trace 映射成 Neural Field 引擎形状。
 */

function clockFromIso(iso) {
  if (!iso) {
    const d = new Date()
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }
  try {
    const d = new Date(String(iso).replace('Z', '+00:00'))
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).format(d)
  } catch {
    return '—'
  }
}

function dateLabelFromIso(iso) {
  try {
    const d = iso ? new Date(String(iso).replace('Z', '+00:00')) : new Date()
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      month: 'numeric',
      day: 'numeric',
      weekday: 'short',
    }).format(d)
  } catch {
    return ''
  }
}

function weatherKind(label) {
  const t = String(label || '')
  if (/雷|暴雨|暴/.test(t)) return 'storm'
  if (/雨/.test(t)) return 'rain'
  if (/多云|阴/.test(t)) return 'cloudy'
  if (/晴/.test(t)) return 'clear'
  return 'cloudy'
}

/** 事件 kind → 中文标题（不向用户暴露英文标识） */
const EVENT_KIND_ZH = {
  world_init: '世界初始化',
  worldline_on: '开启世界线',
  worldline_off: '关闭世界线',
  day_plan: '生成今日安排',
  activity_start: '开始活动',
  activity_complete: '完成活动',
  disturbance: '偶发小事',
  weather: '天气变化',
  weather_change: '天气变化',
  moment_published: '发布动态',
  user_message: '收到对话',
  memory_explicit: '记忆操作',
  turn_trace: '一轮对话',
}

const INTENT_KIND_ZH = {
  message: '主动私聊',
  moment: '发朋友圈',
  proactive: '主动私聊',
  greet: '问候',
}

const CANDIDATE_ZH = {
  chat_followup: '聊天跟进',
  routine_wake: '起床窗口',
  routine_sleep: '睡前窗口',
  world_share: '世界经历',
  world_disturb: '偶发扰动',
  world_weather: '天气变化',
  mood_reachout: '心情找人',
  lonely: '久未联系',
}

const SOURCE_ZH = {
  world: '世界线',
  system: '系统',
  chat: '对话',
}

/** 内部策略码不展示给用户；若必须出现则用中文短名 */
const POLICY_ZH = {
  busy_soft: '可被对话打断',
  busy_hard: '专注中',
  free: '空闲可聊',
}

function looksInternalId(s) {
  return /^[a-z][a-z0-9_]*$/i.test(String(s || '').trim())
}

function eventKindZh(kind) {
  const k = String(kind || '').trim()
  if (!k) return '世界事件'
  if (EVENT_KIND_ZH[k]) return EVENT_KIND_ZH[k]
  if (POLICY_ZH[k]) return POLICY_ZH[k]
  if (looksInternalId(k)) return '世界事件'
  return k
}

function sourceZh(source) {
  const s = String(source || '').trim()
  return SOURCE_ZH[s] || (s && !looksInternalId(s) ? s : '')
}

function userFacingText(value, fallback = '') {
  const t = String(value || '').trim()
  if (!t) return fallback
  if (EVENT_KIND_ZH[t]) return EVENT_KIND_ZH[t]
  if (POLICY_ZH[t]) return POLICY_ZH[t]
  if (looksInternalId(t)) return fallback
  return t
}

function locationZh(loc) {
  const t = String(loc || '').trim()
  if (!t || t === '本地城市') return '所在城市'
  return t
}

function activityStatusLabel(enabled, hasActivity) {
  if (!enabled) return '已暂停'
  if (hasActivity) return '进行中'
  return '空闲中'
}

/**
 * @param {object} board
 * @param {object} worldline
 * @returns {object} WorldlineView / renderWorld 可用的 snapshot
 */
export function toWorldSnapshot(board, worldline) {
  const enabled = !!(worldline?.enabled ?? board?.worldline?.enabled)
  const st = worldline?.state || board?.worldline?.state || {}
  const now = Date.now()
  const weatherLabel = st.weather || (enabled ? '未知' : '—')
  const hasActivity = !!(st.current_activity && String(st.current_activity).trim())
  const activityTitle = hasActivity
    ? String(st.current_activity).trim()
    : enabled
      ? '空闲'
      : '世界线已关闭'
  const location = locationZh(st.location)
  const planBlocks = []
  const plans = worldline?.plans || []
  if (plans[0]?.blocks?.length) {
    for (const b of plans[0].blocks.slice(0, 8)) {
      const title = String(b.title || '')
      let status = 'upcoming'
      const acts = worldline?.activities || []
      const hit = acts.find((a) => a.title === title)
      if (hit?.status === 'running') status = 'current'
      else if (hit?.status === 'completed') status = 'done'
      else if (hit?.status === 'cancelled') status = 'adjusted'
      planBlocks.push({
        id: hit?.id || title,
        time: String(b.start || '').slice(0, 5) || '--:--',
        title,
        status,
        weatherSensitive: /户外|散步|跑步|出门/.test(title),
      })
    }
  }
  if (!planBlocks.length && activityTitle && enabled) {
    planBlocks.push({
      id: 'cur',
      time: clockFromIso(st.updated_at),
      title: activityTitle,
      status: 'current',
      weatherSensitive: false,
    })
  }

  const events = (worldline?.events || []).slice(0, 12).map((e, i) => {
    const payload = e.payload || {}
    const kindZh = eventKindZh(e.kind)
    const summary =
      userFacingText(payload.summary) ||
      userFacingText(payload.title) ||
      (payload.activity ? `开始：${userFacingText(payload.activity, '活动')}` : '') ||
      (e.kind === 'day_plan' && payload.date ? `安排 · ${payload.date}` : '') ||
      kindZh
    const cause = sourceZh(e.source) || '世界线'
    const effectBits = []
    if (payload.activity) effectBits.push(userFacingText(payload.activity))
    const effectText = userFacingText(payload.effect)
    if (effectText) effectBits.push(effectText)
    const ruleText = userFacingText(payload.rule)
    if (ruleText) effectBits.push(ruleText)
    return {
      id: e.id || `ev-${i}`,
      epochMs: Date.parse(e.created_at || '') || now - i * 1000,
      clock: clockFromIso(e.created_at),
      type: e.kind?.includes('weather')
        ? 'weather'
        : e.kind?.includes('disturb')
          ? 'disturbance'
          : e.kind?.includes('activity')
            ? 'activity'
            : 'world',
      summary,
      cause,
      effect: effectBits.filter(Boolean).join(' · ') || '已更新处境',
      decision: '',
      affectDelta: {},
    }
  })

  const running = (worldline?.activities || []).find((a) => a.status === 'running')
  let progress = hasActivity ? 0.35 : 0
  if (running?.starts_at && running?.ends_at) {
    const a = Date.parse(running.starts_at)
    const b = Date.parse(running.ends_at)
    if (b > a) progress = Math.max(0, Math.min(1, (now - a) / (b - a)))
  }

  // 空闲时不要落成 adapted，否则 UI 会显示「因环境调整」
  const statusKey = enabled ? 'running' : 'interrupted'
  const policy = POLICY_ZH[st.interrupt_policy] || ''
  const weatherSource = st.weather_source === 'sim' ? '规则天气' : st.weather_source || ''
  const proactive = board?.proactive || {}
  const budget = proactive.budget || {}
  const openIntents = (board?.intents || []).slice(0, 6)
  const topIntent = openIntents[0]
  let intentHint = ''
  if (topIntent) {
    const kindZh = INTENT_KIND_ZH[topIntent.action_kind] || topIntent.action_kind || '意图'
    intentHint = `${kindZh}：${userFacingText(topIntent.reason_summary, '待执行')}`
  } else if (budget.message_left != null) {
    intentHint = `今日私聊剩余 ${budget.message_left}/${budget.message_cap ?? 4}`
  }
  const concernBits = []
  if (policy) concernBits.push(policy)
  if (st.next_wake_at) concernBits.push(`下次推进 ${clockFromIso(st.next_wake_at)}`)
  if (budget.unanswered_streak >= 2) concernBits.push('主动未回，预算已收紧')

  return {
    revision: Number(board?.state_version || st.revision || 0),
    epochMs: now,
    clock: clockFromIso(st.updated_at),
    dateLabel: dateLabelFromIso(st.updated_at),
    mode: 'realtime',
    speed: 1,
    weather: {
      kind: weatherKind(weatherLabel),
      label: weatherLabel,
      temperature: null,
      until: '',
      reason: weatherSource,
    },
    activity: {
      id: st.current_activity_id || 'none',
      title: activityTitle,
      location,
      status: statusKey,
      statusLabel: activityStatusLabel(enabled, hasActivity),
      startedAt: running?.starts_at ? Date.parse(running.starts_at) : now - 600000,
      endsAt: running?.ends_at ? Date.parse(running.ends_at) : now + 600000,
      progress,
      reason: policy ? `打断策略：${policy}` : '',
    },
    plan: planBlocks,
    disturbance: { active: false, title: '', until: '', reason: '' },
    concern: concernBits.join(' · '),
    intent: intentHint,
    summary: enabled ? `${weatherLabel} · ${activityTitle}` : '世界线关闭',
    nextAt: st.next_wake_at || '',
    events,
    enabled,
    weatherSource,
    interruptPolicy: policy,
    proactive,
  }
}

/**
 * 把 board 灌进 CognitiveDemoEngine.state（不写后端）。
 */
export function hydrateEngine(engine, board, worldSnapshot) {
  if (!engine?.state) return
  const affect = board?.affect || {}
  const dims = ['joy', 'sadness', 'excitement', 'worry', 'annoyance', 'curiosity']
  for (const k of dims) {
    if (affect[k] != null) engine.state.emotion[k] = Number(affect[k]) || 0
  }
  const facts = (board?.memories || []).map((m) => ({
    id: m.id,
    key: m.id,
    value: m.text,
    text: m.text,
    status: m.status === 'superseded' ? 'superseded' : 'active',
    heat: typeof m.heat === 'number' ? m.heat : 40,
    origin: m.source_kind || 'live',
  }))
  engine.state.facts = facts
  if (worldSnapshot) {
    engine.state.world = {
      clock: worldSnapshot.clock,
      day: 1,
      location: worldSnapshot.activity.location,
      activity: worldSnapshot.activity.title,
      concern: worldSnapshot.concern,
      next: worldSnapshot.intent,
      revision: worldSnapshot.revision,
      domain: worldSnapshot.enabled ? 'live' : 'off',
    }
  }
}

/**
 * turn-trace → 本地「过程动画」用的 proposal（不 commit 到假权威）。
 */
export function traceToProposal(trace) {
  if (!trace) return null
  const mems = trace.memories_used || []
  const explicit = trace.explicit || {}
  const mode =
    explicit.op === 'correct' || /更正|改名|本来叫/.test(trace.user_text || '')
      ? 'correction'
      : explicit.op === 'remember' || /记住/.test(trace.user_text || '')
        ? 'custom'
        : mems.length
          ? 'recall'
          : 'small-talk'
  return {
    id: trace.id,
    input: trace.user_text || '',
    kind: 'chat',
    mode,
    response: trace.assistant_text || '',
    appraisal: trace.affect_strategy || '主路径理解完成',
    intention: trace.world_presence || '按当前状态回应',
    emotionDelta: {},
    facts: mems.map((m) => ({
      id: m.id,
      key: m.id,
      text: m.text,
      status: 'active',
      heat: m.heat ?? 45,
      origin: 'live',
    })),
    recall: mems.map((m) => ({ id: m.id, text: m.text })),
    segments: [
      { type: 'fact', text: (mems[0] && mems[0].text) || '本轮证据' },
      { type: 'intent', text: (trace.world_presence || '立即回复').slice(0, 40) },
    ],
    episode: null,
    worldUpdate: null,
    created_at: trace.created_at,
  }
}

export function formatMonitorMeta(board, worldline, latestTrace) {
  const nameBits = []
  if (latestTrace?.created_at) nameBits.push(`最近 ${clockFromIso(latestTrace.created_at)}`)
  const wl = worldline?.enabled ?? board?.worldline?.enabled
  nameBits.push(wl ? '世界线开' : '世界线关')
  const bud = board?.proactive?.budget
  if (bud && bud.message_left != null) {
    nameBits.push(`主动${bud.messages_sent || 0}/${bud.message_cap || 4}`)
  }
  return nameBits.join(' · ')
}

/**
 * 监控页右侧/底部真实面板：心情来源、待发意图、预算、最近回合。
 */
export function toMonitorPanels(board, worldline, traces) {
  const affectEvents = (board?.affect_events || []).slice(0, 8).map((e) => {
    const src = e.source === 'world' || String(e.reason || '').startsWith('world:') ? 'world' : 'chat'
    let deltas = {}
    try {
      deltas = typeof e.deltas_json === 'string' ? JSON.parse(e.deltas_json || '{}') : e.deltas || {}
    } catch {
      deltas = {}
    }
    const deltaBits = Object.entries(deltas)
      .filter(([, v]) => Number(v))
      .map(([k, v]) => `${k}${Number(v) > 0 ? '+' : ''}${v}`)
      .slice(0, 3)
    return {
      id: e.id || e.event_id || e.created_at,
      source: src,
      sourceLabel: src === 'world' ? '世界线' : '对话',
      reason: String(e.reason || '').replace(/^world:/, '') || '心情变化',
      deltas: deltaBits.join(' '),
      clock: clockFromIso(e.created_at),
    }
  })

  const intents = (board?.intents || []).slice(0, 8).map((it) => {
    let payload = {}
    try {
      payload = typeof it.payload_json === 'string' ? JSON.parse(it.payload_json || '{}') : it.payload || {}
    } catch {
      payload = {}
    }
    const ctype = payload.candidate_type || ''
    return {
      id: it.id,
      kind: INTENT_KIND_ZH[it.action_kind] || it.action_kind || '意图',
      status: it.status || '',
      ctype: CANDIDATE_ZH[ctype] || ctype,
      reason: userFacingText(it.reason_summary, '待执行'),
      when: it.not_before ? clockFromIso(it.not_before) : '待定',
      source: it.source === 'chat' ? '对话' : '世界线',
    }
  })

  const bud = board?.proactive?.budget || {}
  const lastPlan = board?.proactive?.last_plan || null
  const skipped = (lastPlan?.skipped || []).slice(0, 5).map((s) => ({
    reason: s.reason || '',
    type: CANDIDATE_ZH[s.type] || s.type || '',
  }))

  const recentTraces = (traces || []).slice(0, 6).map((t) => ({
    id: t.id,
    user: (t.user_text || '').slice(0, 80),
    assistant: (t.assistant_text || '').slice(0, 100),
    presence: t.world_presence || '',
    clock: clockFromIso(t.created_at),
    mems: (t.memories_used || []).length,
  }))

  // 原始档案：回合 + 世界事件
  const archive = []
  for (const t of traces || []) {
    archive.push({
      id: t.id,
      kind: 'chat',
      text: t.user_text || '',
      reply: t.assistant_text || '',
      at: clockFromIso(t.created_at),
      day: 1,
      dateLabel: dateLabelFromIso(t.created_at),
      origin: 'live_trace',
    })
  }
  for (const e of (worldline?.events || []).slice(0, 10)) {
    const payload = e.payload || {}
    archive.push({
      id: e.id || `we-${e.created_at}`,
      kind: 'world',
      text: userFacingText(payload.summary || payload.title, eventKindZh(e.kind)),
      reply: sourceZh(e.source) || '世界线',
      at: clockFromIso(e.created_at),
      day: 1,
      dateLabel: dateLabelFromIso(e.created_at),
      origin: 'world_event',
    })
  }

  return {
    affectEvents,
    intents,
    budget: {
      messagesSent: bud.messages_sent ?? 0,
      messageCap: bud.message_cap ?? 4,
      messageLeft: bud.message_left ?? null,
      momentsSent: bud.moments_sent ?? 0,
      momentCap: bud.moment_cap ?? 2,
      unanswered: bud.unanswered_streak ?? 0,
    },
    lastPlan: lastPlan
      ? {
          trigger: lastPlan.trigger || '',
          at: clockFromIso(lastPlan.created_at),
          accepted: (lastPlan.accepted || []).length,
          skipped,
        }
      : null,
    recentTraces,
    archive,
  }
}
