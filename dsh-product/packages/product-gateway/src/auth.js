/**
 * 网关鉴权：
 * - Bearer <DSH_GATEWAY_TOKEN>：FastAPI 服务凭证（可代任意 userId）
 * - Bearer <产品 access JWT>：HS256，payload.sub 必须等于 body.userId
 */
import crypto from 'node:crypto'

const serviceToken = process.env.DSH_GATEWAY_TOKEN || ''
const jwtSecret = process.env.PRODUCT_JWT_SECRET || ''

function b64urlJson(buf) {
  return JSON.parse(Buffer.from(buf, 'base64url').toString('utf8'))
}

export function verifyProductJwt(token) {
  if (!jwtSecret || !token) return null
  const parts = token.split('.')
  if (parts.length !== 3) return null
  const [h, p, s] = parts
  const expected = crypto.createHmac('sha256', jwtSecret).update(`${h}.${p}`).digest('base64url')
  const a = Buffer.from(expected)
  const b = Buffer.from(s)
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null
  let payload
  try {
    payload = b64urlJson(p)
  } catch {
    return null
  }
  if (payload.typ !== 'access') return null
  if (typeof payload.exp === 'number') {
    const expMs = payload.exp < 1e12 ? payload.exp * 1000 : payload.exp
    if (expMs < Date.now()) return null
  }
  if (typeof payload.sub !== 'string' || !payload.sub) return null
  return payload
}

/**
 * @returns {{ ok: true, mode: 'service'|'user', userId?: string } | { ok: false, error: string }}
 */
export function authorize(req, bodyUserId) {
  const h = req.headers.authorization || ''
  if (!h.startsWith('Bearer ')) return { ok: false, error: 'missing bearer' }
  const raw = h.slice(7).trim()
  if (!raw) return { ok: false, error: 'missing bearer' }

  if (serviceToken && raw === serviceToken) {
    return { ok: true, mode: 'service' }
  }

  const payload = verifyProductJwt(raw)
  if (payload) {
    if (bodyUserId && payload.sub !== bodyUserId) {
      return { ok: false, error: 'jwt sub mismatch userId' }
    }
    return { ok: true, mode: 'user', userId: payload.sub }
  }

  if (!serviceToken && !jwtSecret) return { ok: true, mode: 'service' } // 开发无密钥
  return { ok: false, error: 'unauthorized' }
}
