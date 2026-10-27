/**
 * P0 smoke: companion + DeepSeek via SDK.
 * 设计在服务器 Docker 内跑；本机仅调试插件逻辑，不作为日常算力。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { DeepSeekHarness } from '@deepseek-ai/dsh-sdk-client'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = process.env.PRODUCT_ROOT
  ? path.resolve(process.env.PRODUCT_ROOT)
  : path.resolve(__dirname, '..')
const dshHome = process.env.DSH_HOME
  ? path.resolve(process.env.DSH_HOME)
  : path.join(root, '.dsh-home')
const patch = path.join(root, 'profiles', 'companion.product.patch.yml')
const mapFile = path.join(dshHome, 'p0-session-map.json')
const dshBin = path.join(root, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js')

const provider = process.env.P0_PROVIDER || 'deepseek-official'
const model = process.env.P0_MODEL || 'deepseek-flash'

function loadMap() {
  try {
    return JSON.parse(fs.readFileSync(mapFile, 'utf8'))
  } catch {
    return {}
  }
}

function saveMap(map) {
  fs.mkdirSync(path.dirname(mapFile), { recursive: true })
  fs.writeFileSync(mapFile, JSON.stringify(map, null, 2))
}

async function main() {
  const mode = process.argv[2] || 'run'
  const map = loadMap()
  const key = 'p0-user:p0-conversation'

  if (!fs.existsSync(dshBin)) {
    throw new Error(`dsh bin missing: ${dshBin} (run inside image or npm ci first)`)
  }

  console.error(`[p0-smoke] PRODUCT_ROOT=${root} DSH_HOME=${dshHome} provider=${provider} model=${model} mode=${mode}`)

  await using harness = new DeepSeekHarness({
    profile: 'companion',
    dshHome,
    dshBin,
    patches: [patch],
    provider,
    model,
    cwd: root,
    initializeTimeoutMs: 120_000,
  })

  if (mode === 'resume') {
    const saved = map[key]
    if (!saved?.dshSessionId) {
      throw new Error(`no saved session for ${key}; run without resume first`)
    }
    console.error(`[p0-smoke] resumeSession ${saved.dshSessionId}`)
    const session = await harness.resumeSession(saved.dshSessionId)
    const result = await session.run('只回复两个字：还在')
    console.log(JSON.stringify({
      ok: true,
      mode: 'resume',
      sessionId: result.sessionId,
      finalResponse: result.finalResponse,
      eventCount: result.events?.length ?? 0,
    }, null, 2))
    return
  }

  const result = await harness.run('用不超过十五个汉字介绍你自己，不要表情。')
  map[key] = {
    productUserId: 'p0-user',
    productConversationId: 'p0-conversation',
    dshSessionId: result.sessionId,
    updatedAt: Date.now(),
  }
  saveMap(map)
  console.log(JSON.stringify({
    ok: true,
    mode: 'run',
    sessionId: result.sessionId,
    finalResponse: result.finalResponse,
    eventCount: result.events?.length ?? 0,
    mapFile,
  }, null, 2))
}

main().catch((err) => {
  console.error('[p0-smoke] FAILED', err)
  process.exitCode = 1
})
