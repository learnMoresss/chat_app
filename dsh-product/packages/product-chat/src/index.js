import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'

const require = createRequire(import.meta.url)

export const name = 'product-chat'
export const inject = ['agents', 'sessions']

function dbPath() {
  return process.env.CHAT_APP_PRODUCT_DB
    || path.resolve(process.env.DSH_HOME || '.', 'product-chat.sqlite')
}

function openDb() {
  // Prefer better-sqlite3 if present; else JSONL fallback for P0 scaffold.
  try {
    const Database = require('better-sqlite3')
    const db = new Database(dbPath())
    db.exec(`
      CREATE TABLE IF NOT EXISTS dsh_session_map (
        product_user_id TEXT NOT NULL,
        product_conversation_id TEXT NOT NULL,
        dsh_session_id TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (product_user_id, product_conversation_id)
      );
      CREATE TABLE IF NOT EXISTS dsh_message_projection (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dsh_session_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        role TEXT,
        text TEXT,
        created_at INTEGER NOT NULL
      );
    `)
    return { kind: 'sqlite', db }
  } catch {
    const file = dbPath().replace(/\.sqlite$/, '') + '.jsonl'
    fs.mkdirSync(path.dirname(file), { recursive: true })
    return { kind: 'jsonl', file }
  }
}

function projectAssistant(store, sessionId, text) {
  const now = Date.now()
  if (store.kind === 'sqlite') {
    store.db.prepare(
      `INSERT INTO dsh_message_projection(dsh_session_id, event_type, role, text, created_at)
       VALUES (?, 'assistant/message', 'assistant', ?, ?)`,
    ).run(sessionId, text, now)
  } else {
    fs.appendFileSync(store.file, JSON.stringify({
      dsh_session_id: sessionId,
      event_type: 'assistant/message',
      role: 'assistant',
      text,
      created_at: now,
    }) + '\n')
  }
}

export function apply(ctx) {
  const store = openDb()
  console.error(`[product-chat] loaded store=${store.kind} path=${store.kind === 'sqlite' ? dbPath() : store.file}`)

  ctx.on('session/event', (session, event) => {
    if (event?.type !== 'assistant/message' && event?.type !== 'assistant/attempt') return
    const sid = session?.id || session?.header?.id || 'unknown'
    let text = ''
    try {
      const stream = event?.data?.stream
      if (Array.isArray(stream)) {
        for (const frame of stream) {
          const chunk = frame?.chunk ?? frame
          if (chunk?.type === 'text-delta' && typeof chunk.text === 'string') text += chunk.text
        }
      } else if (typeof event?.data?.text === 'string') {
        text = event.data.text
      }
    } catch {}
    if (text) projectAssistant(store, String(sid), text)
  })
}
