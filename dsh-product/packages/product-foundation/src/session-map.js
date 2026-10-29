/**
 * 产品 conversation ↔ DSH session 映射（P0）。
 * 落在 DSH_HOME 旁的 product DB，不写进上游 session 日志当产品真相。
 */
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

export function productDbPath() {
  if (process.env.CHAT_APP_PRODUCT_DB) return process.env.CHAT_APP_PRODUCT_DB
  const home = process.env.DSH_HOME || process.cwd()
  return path.join(home, 'product.sqlite')
}

export function openProductDb() {
  const Database = require('better-sqlite3')
  const file = productDbPath()
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const db = new Database(file)
  db.exec(`
    CREATE TABLE IF NOT EXISTS dsh_session_map (
      product_user_id TEXT NOT NULL,
      product_conversation_id TEXT NOT NULL,
      dsh_session_id TEXT NOT NULL,
      updated_at INTEGER NOT NULL,
      PRIMARY KEY (product_user_id, product_conversation_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_dsh_session_id
      ON dsh_session_map(dsh_session_id);
  `)
  // provider/model：跨 harness 池恢复会话
  const cols = db.prepare(`PRAGMA table_info(dsh_session_map)`).all().map((c) => c.name)
  if (!cols.includes('provider')) {
    db.exec(`ALTER TABLE dsh_session_map ADD COLUMN provider TEXT NOT NULL DEFAULT ''`)
  }
  if (!cols.includes('model')) {
    db.exec(`ALTER TABLE dsh_session_map ADD COLUMN model TEXT NOT NULL DEFAULT ''`)
  }
  return db
}

export function getMappedSession(db, userId, conversationId) {
  return db.prepare(
    `SELECT dsh_session_id AS dshSessionId, updated_at AS updatedAt,
            COALESCE(provider,'') AS provider, COALESCE(model,'') AS model
     FROM dsh_session_map
     WHERE product_user_id = ? AND product_conversation_id = ?`,
  ).get(userId, conversationId)
}

export function upsertMappedSession(db, userId, conversationId, dshSessionId, provider = '', model = '') {
  db.prepare(
    `INSERT INTO dsh_session_map(product_user_id, product_conversation_id, dsh_session_id, updated_at, provider, model)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(product_user_id, product_conversation_id) DO UPDATE SET
       dsh_session_id = excluded.dsh_session_id,
       updated_at = excluded.updated_at,
       provider = excluded.provider,
       model = excluded.model`,
  ).run(userId, conversationId, dshSessionId, Date.now(), provider || '', model || '')
}
