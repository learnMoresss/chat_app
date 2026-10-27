/**
 * Node 侧设备 API 客户端：强制读 product-vd/env.sh 等价环境变量。
 * agent shell 仍推荐 vd-api.sh；本模块给脚本/插件用。
 */
import fs from 'node:fs'
import path from 'node:path'

function loadEnvFile() {
  const home = process.env.DSH_HOME || process.cwd()
  const p = path.join(home, 'product-vd', 'env.sh')
  if (!fs.existsSync(p)) return
  const text = fs.readFileSync(p, 'utf8')
  for (const line of text.split('\n')) {
    const m = line.match(/^export\s+([A-Z0-9_]+)=(.*)$/)
    if (!m) continue
    let v = m[2].trim()
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1)
    }
    if (!process.env[m[1]]) process.env[m[1]] = v
  }
}

export async function vdApi(method, apiPath, body) {
  loadEnvFile()
  const base = (process.env.CHAT_API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '')
  const token = process.env.DSH_GATEWAY_TOKEN || ''
  const ticket = process.env.VD_TICKET || ''
  const uid = process.env.CHAT_USER_ID || ''
  if (!token) throw new Error('DSH_GATEWAY_TOKEN missing')
  if (!ticket && !uid) throw new Error('VD_TICKET/CHAT_USER_ID missing')
  const headers = {
    Authorization: `Bearer ${token}`,
    'X-Persona-Id': process.env.CHAT_PERSONA_ID || '',
    'X-Conv-Id': process.env.CHAT_CONV_ID || '',
  }
  if (ticket) headers['X-Vd-Ticket'] = ticket
  if (uid) headers['X-Chat-User-Id'] = uid
  const init = { method: method.toUpperCase(), headers }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = typeof body === 'string' ? body : JSON.stringify(body)
  }
  const url = `${base}/api/v1/virtual-devices/${String(apiPath).replace(/^\//, '')}`
  const res = await fetch(url, init)
  const text = await res.text()
  try {
    return JSON.parse(text)
  } catch {
    return { ok: res.ok, status: res.status, raw: text.slice(0, 500) }
  }
}
