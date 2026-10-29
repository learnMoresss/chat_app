/**
 * 产品侧驱动 DSH runtime（SDK 客户端）。
 * 按 provider/model 建 harness 小池；同一会话用 stickyKey 粘滞到固定槽位，
 * 避免跨实例 resume 失败。
 */
import path from 'node:path'
import { createHash } from 'node:crypto'
import { DeepSeekHarness } from '@deepseek-ai/dsh-sdk-client'

/** @type {Map<string, import('@deepseek-ai/dsh-sdk-client').DeepSeekHarness[]>} */
const harnessPools = new Map()

export function defaultProvider() {
  return process.env.P0_PROVIDER || 'daycard-deepseek'
}

export function defaultModel() {
  return process.env.P0_MODEL || 'deepseek-v4.1-flash'
}

export function maxHarnessPerRoute() {
  const n = Number(process.env.DSH_HARNESS_POOL || process.env.DSH_MAX_CONCURRENCY || 3)
  return Math.max(1, Math.min(8, Number.isFinite(n) ? n : 3))
}

export function harnessKey(provider, model) {
  return `${provider || defaultProvider()}::${model || defaultModel()}`
}

export function createProductHarness(options = {}) {
  const root = path.resolve(process.env.PRODUCT_ROOT || '/app')
  const dshHome = path.resolve(process.env.DSH_HOME || '/dsh-home')
  const provider = options.provider || defaultProvider()
  const model = options.model || defaultModel()
  return new DeepSeekHarness({
    profile: options.profile || 'companion',
    dshHome,
    dshBin: path.join(root, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js'),
    patches: [path.join(root, 'profiles', 'companion.product.patch.yml')],
    provider,
    model,
    cwd: root,
    initializeTimeoutMs: options.initializeTimeoutMs ?? 120_000,
  })
}

function slotFor(stickyKey, max) {
  if (!stickyKey || max <= 1) return 0
  const h = createHash('sha1').update(String(stickyKey)).digest()
  return h.readUInt32BE(0) % max
}

/**
 * @param {{ provider?: string, model?: string, stickyKey?: string }} [options]
 */
export function getPooledHarness(options = {}) {
  const provider = options.provider || defaultProvider()
  const model = options.model || defaultModel()
  const key = harnessKey(provider, model)
  let pool = harnessPools.get(key)
  if (!pool) {
    pool = []
    harnessPools.set(key, pool)
  }
  const max = maxHarnessPerRoute()
  while (pool.length < max) {
    pool.push(createProductHarness({ provider, model }))
  }
  const slot = slotFor(options.stickyKey || '', pool.length)
  return { harness: pool[slot], provider, model, key, slot }
}

export async function closeAllHarnesses() {
  const all = [...harnessPools.values()].flat()
  harnessPools.clear()
  for (const h of all) {
    try {
      await h.close()
    } catch {
      /* ignore */
    }
  }
}
