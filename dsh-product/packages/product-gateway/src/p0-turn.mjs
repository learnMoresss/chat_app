/**
 * P0：按产品 user + conversation 跑一轮（create 或 resume），供服务器容器内调用。
 * 用法：
 *   node packages/product-gateway/src/p0-turn.mjs <userId> <conversationId> <text>
 */
import {
  openProductDb,
  getMappedSession,
  upsertMappedSession,
} from '../../product-foundation/src/session-map.js'
import { createProductHarness } from './dsh-runtime.js'

async function main() {
  const [userId, conversationId, ...rest] = process.argv.slice(2)
  const text = rest.join(' ').trim()
  if (!userId || !conversationId || !text) {
    console.error('usage: p0-turn.mjs <userId> <conversationId> <text>')
    process.exitCode = 2
    return
  }

  let db
  try {
    db = openProductDb()
  } catch (err) {
    console.error('[p0-turn] better-sqlite3 required in image:', err.message)
    process.exitCode = 1
    return
  }

  await using harness = createProductHarness()
  const mapped = getMappedSession(db, userId, conversationId)

  let result
  if (mapped?.dshSessionId) {
    console.error(`[p0-turn] resume ${mapped.dshSessionId}`)
    const session = await harness.resumeSession(mapped.dshSessionId)
    result = await session.run(text)
  } else {
    console.error('[p0-turn] create')
    result = await harness.run(text)
    upsertMappedSession(db, userId, conversationId, result.sessionId)
  }

  if (mapped?.dshSessionId !== result.sessionId) {
    upsertMappedSession(db, userId, conversationId, result.sessionId)
  }

  console.log(JSON.stringify({
    ok: true,
    productUserId: userId,
    productConversationId: conversationId,
    dshSessionId: result.sessionId,
    finalResponse: result.finalResponse,
  }, null, 2))
}

main().catch((err) => {
  console.error('[p0-turn] FAILED', err)
  process.exitCode = 1
})
