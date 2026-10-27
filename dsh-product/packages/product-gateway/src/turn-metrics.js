/**
 * 从 DSH RunResult 抽取本轮 token / 缓存命中，并估算产品侧上下文体积。
 */

function walkUsage(node, out = []) {
  if (node == null || typeof node !== 'object') return out
  if (typeof node.inputTokens === 'number' && typeof node.outputTokens === 'number') {
    out.push({
      inputTokens: Number(node.inputTokens) || 0,
      outputTokens: Number(node.outputTokens) || 0,
      cacheReadTokens: Number(node.cacheReadTokens) || 0,
      cacheWriteTokens: Number(node.cacheWriteTokens) || 0,
      totalTokens: node.totalTokens != null ? Number(node.totalTokens) : undefined,
      reasoningTokens: Number(node.reasoningTokens) || 0,
    })
    return out
  }
  if (Array.isArray(node)) {
    for (const item of node) walkUsage(item, out)
    return out
  }
  for (const v of Object.values(node)) {
    if (v && typeof v === 'object') walkUsage(v, out)
  }
  return out
}

function sumUsages(usages) {
  const acc = {
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    reasoningTokens: 0,
    modelCalls: usages.length,
  }
  for (const u of usages) {
    acc.inputTokens += u.inputTokens
    acc.outputTokens += u.outputTokens
    acc.cacheReadTokens += u.cacheReadTokens
    acc.cacheWriteTokens += u.cacheWriteTokens
    acc.reasoningTokens += u.reasoningTokens || 0
  }
  return acc
}

/**
 * @param {{
 *   prompt: string,
 *   isNew: boolean,
 *   history?: Array<unknown>,
 *   system?: string,
 *   instructions?: string,
 *   text?: string,
 *   result?: { events?: unknown[], notifications?: unknown[], sessionId?: string },
 *   mode?: string,
 * }} opts
 */
export function buildTurnMetrics(opts) {
  const prompt = String(opts.prompt || '')
  const system = String(opts.system || '')
  const instructions = String(opts.instructions || '')
  const text = String(opts.text || '')
  const history = Array.isArray(opts.history) ? opts.history : []
  const historyChars = history.reduce((n, m) => n + String(m?.content || '').length, 0)

  const product = {
    promptChars: prompt.length,
    promptTokensEst: Math.max(1, Math.ceil(prompt.length / 4)),
    systemChars: system.length,
    instructionsChars: instructions.length,
    historyMessages: history.length,
    historyChars,
    userTextChars: text.length,
    isNewSession: Boolean(opts.isNew),
    resumed: !opts.isNew,
    mode: opts.mode || 'chat',
  }

  const result = opts.result || {}
  const usages = [
    ...walkUsage(result.events || []),
    ...walkUsage(result.notifications || []),
  ]
  // 去重：同一 usage 可能同时出现在 event 与 notification
  const seen = new Set()
  const unique = []
  for (const u of usages) {
    const key = `${u.inputTokens}:${u.outputTokens}:${u.cacheReadTokens}:${u.cacheWriteTokens}`
    if (seen.has(key)) continue
    seen.add(key)
    unique.push(u)
  }
  const summed = sumUsages(unique)
  const contextTokens =
    summed.inputTokens + summed.cacheReadTokens + summed.cacheWriteTokens
  const cacheHitRate =
    contextTokens > 0
      ? Math.round((summed.cacheReadTokens / contextTokens) * 10000) / 100
      : 0

  return {
    ...product,
    dshSessionId: result.sessionId || '',
    modelCalls: summed.modelCalls,
    inputTokens: summed.inputTokens,
    outputTokens: summed.outputTokens,
    cacheReadTokens: summed.cacheReadTokens,
    cacheWriteTokens: summed.cacheWriteTokens,
    reasoningTokens: summed.reasoningTokens,
    contextTokens: contextTokens || product.promptTokensEst,
    cacheHitRate,
    source: unique.length ? 'dsh' : 'estimate',
  }
}
