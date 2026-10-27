const LS = {
  access: 'chat_access',
  refresh: 'chat_refresh',
  base: 'chat_base',
  user: 'chat_user',
  remember: 'chat_remember',
  username: 'chat_username',
} as const

export type User = {
  id?: string
  username?: string
  display_name?: string
  is_admin?: boolean
}

export type Session = {
  token: string
  refresh: string
  base: string
  user: User
  remember: boolean
}

function friendlyDetail(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail) && detail.length) {
    const parts: string[] = []
    for (const item of detail) {
      if (!item || typeof item !== 'object') continue
      const row = item as { loc?: unknown; type?: string; msg?: string }
      const loc = Array.isArray(row.loc) ? row.loc : []
      const field = loc.length ? String(loc[loc.length - 1]) : ''
      const type = row.type || ''
      const raw = row.msg || ''
      if ((field === 'password' || field === 'new_password') && (type.includes('too_short') || raw.includes('at least'))) {
        parts.push('密码至少 8 位')
        continue
      }
      if (field === 'username' && (type.includes('too_short') || raw.includes('at least'))) {
        parts.push('用户名至少 2 个字符')
        continue
      }
      if (raw && !raw.includes('String should')) {
        parts.push(raw)
        continue
      }
      if (field) parts.push(`${field} 填写有误`)
    }
    if (parts.length) return parts.join('；')
  }
  if (status === 422) return '填写有误，请检查后再试'
  return `请求失败 ${status}`
}

function isLoopbackBase(base: string): boolean {
  try {
    const u = new URL(base)
    return u.hostname === '127.0.0.1' || u.hostname === 'localhost'
  } catch {
    return false
  }
}

function readSession(): Session {
  let base = localStorage.getItem(LS.base) || ''
  // 公网页面若仍残留本机地址，一律丢掉，否则全站请求打到浏览器本机 → 直接报错
  if (
    typeof window !== 'undefined' &&
    window.location.pathname.startsWith('/chat-api') &&
    isLoopbackBase(base)
  ) {
    base = ''
    localStorage.removeItem(LS.base)
  }
  return {
    token: localStorage.getItem(LS.access) || '',
    refresh: localStorage.getItem(LS.refresh) || '',
    base,
    user: JSON.parse(localStorage.getItem(LS.user) || '{}') as User,
    remember: localStorage.getItem(LS.remember) !== '0',
  }
}

let session = readSession()

export function getSession() {
  return session
}

export function getSavedUsername() {
  return localStorage.getItem(LS.username) || ''
}

/** 当前页所在 API 根：公网多为 https://host:8443/chat-api ，本机多为 http://127.0.0.1:8000 */
export function defaultApiBase(): string {
  if (typeof window === 'undefined') return ''
  const { origin, pathname } = window.location
  const m = pathname.match(/^(\/chat-api)(?:\/|$)/)
  if (m) return `${origin}${m[1]}`
  return origin
}

export function apiBase(override?: string) {
  let raw = (override ?? session.base ?? '').trim()
  if (
    typeof window !== 'undefined' &&
    window.location.pathname.startsWith('/chat-api') &&
    isLoopbackBase(raw)
  ) {
    raw = ''
  }
  if (!raw) return defaultApiBase()
  // 误填成站点根且实际挂在 /chat-api 时自动补前缀
  const cleaned = raw.replace(/\/$/, '')
  try {
    const u = new URL(cleaned, window.location.origin)
    if (
      typeof window !== 'undefined' &&
      window.location.pathname.startsWith('/chat-api') &&
      u.origin === window.location.origin &&
      !u.pathname.replace(/\/$/, '').endsWith('/chat-api')
    ) {
      return `${u.origin}/chat-api`
    }
  } catch {
    /* ignore */
  }
  return cleaned
}

export function persistSession(next: Partial<Session> & { username?: string }) {
  session = { ...session, ...next }
  if (next.username) localStorage.setItem(LS.username, next.username)
  localStorage.setItem(LS.base, session.base || '')
  localStorage.setItem(LS.remember, session.remember ? '1' : '0')
  if (!session.remember) {
    localStorage.removeItem(LS.access)
    localStorage.removeItem(LS.refresh)
    localStorage.removeItem(LS.user)
    return
  }
  localStorage.setItem(LS.access, session.token || '')
  localStorage.setItem(LS.refresh, session.refresh || '')
  localStorage.setItem(LS.user, JSON.stringify(session.user || {}))
}

export function clearSession(keepUsername = true) {
  localStorage.removeItem(LS.access)
  localStorage.removeItem(LS.refresh)
  localStorage.removeItem(LS.user)
  if (!keepUsername) localStorage.removeItem(LS.username)
  session = { ...session, token: '', refresh: '', user: {} }
}

async function tryRefresh(): Promise<boolean> {
  if (!session.refresh) return false
  try {
    const res = await fetch(apiBase() + '/api/v1/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: session.refresh }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return false
    persistSession({
      token: data.access_token || '',
      refresh: data.refresh_token || session.refresh,
      user: data.user || session.user,
      remember: true,
    })
    return !!session.token
  } catch {
    return false
  }
}

export async function api<T = unknown>(
  path: string,
  opts: RequestInit = {},
  { retry = true }: { retry?: boolean } = {},
): Promise<T> {
  const headers = new Headers(opts.headers || {})
  if (!(opts.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (session.token) headers.set('Authorization', `Bearer ${session.token}`)

  const res = await fetch(apiBase() + path, { ...opts, headers })
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }

  if (res.status === 401 && retry && session.refresh && !path.includes('/auth/')) {
    const ok = await tryRefresh()
    if (ok) return api(path, opts, { retry: false })
  }

  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail
    throw new Error(friendlyDetail(detail, res.status))
  }
  return data as T
}

export async function login(input: {
  username: string
  password: string
  base?: string
  remember: boolean
}) {
  if (input.base !== undefined) {
    const b = (input.base || '').trim().replace(/\/$/, '')
    // 公网留空；勿把 127.0.0.1 写进 localStorage
    session.base =
      typeof window !== 'undefined' &&
      window.location.pathname.startsWith('/chat-api') &&
      (!b || isLoopbackBase(b))
        ? ''
        : b
  }
  session.remember = input.remember
  const data = await api<{
    access_token: string
    refresh_token: string
    user: User
  }>(
    '/api/v1/auth/login',
    {
      method: 'POST',
      body: JSON.stringify({
        username: input.username,
        password: input.password,
        device_name: 'WebAdmin',
      }),
    },
    { retry: false },
  )
  persistSession({
    token: data.access_token,
    refresh: data.refresh_token || '',
    user: data.user || {},
    base: apiBase(input.base),
    remember: input.remember,
    username: input.username,
  })
  return data
}

export async function logout() {
  try {
    if (session.refresh) {
      await fetch(apiBase() + '/api/v1/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: session.refresh }),
      })
    }
  } catch {
    /* ignore */
  }
  clearSession(true)
}

export async function downloadCsv(path: string, filename: string) {
  const headers: Record<string, string> = {}
  if (session.token) headers.Authorization = `Bearer ${session.token}`
  const res = await fetch(apiBase() + path, { headers })
  if (!res.ok) {
    const text = await res.text()
    let detail = `下载失败 ${res.status}`
    try {
      const j = JSON.parse(text)
      if (typeof j.detail === 'string') detail = j.detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export async function bootSession(): Promise<boolean> {
  session = readSession()
  if (!session.token && !session.refresh) return false
  try {
    await api('/api/v1/prefs')
    return true
  } catch {
    const ok = await tryRefresh()
    if (!ok) {
      clearSession(true)
      return false
    }
    try {
      await api('/api/v1/prefs')
      return true
    } catch {
      clearSession(true)
      return false
    }
  }
}
