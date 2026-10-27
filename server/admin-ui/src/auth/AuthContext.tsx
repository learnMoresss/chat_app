import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import {
  bootSession,
  getSession,
  login as apiLogin,
  logout as apiLogout,
  type User,
} from '../api/client'

type AuthState = {
  ready: boolean
  authed: boolean
  user: User
  serverLabel: string
  login: (v: {
    username: string
    password: string
    base?: string
    remember: boolean
  }) => Promise<void>
  logout: () => Promise<void>
  setServerLabel: (s: string) => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false)
  const [authed, setAuthed] = useState(false)
  const [user, setUser] = useState<User>({})
  const [serverLabel, setServerLabel] = useState('管理台')

  useEffect(() => {
    bootSession()
      .then((ok) => {
        setAuthed(ok)
        if (ok) setUser(getSession().user)
      })
      .finally(() => setReady(true))
  }, [])

  const login = useCallback(async (v: {
    username: string
    password: string
    base?: string
    remember: boolean
  }) => {
    const data = await apiLogin(v)
    setUser(data.user || {})
    setAuthed(true)
  }, [])

  const logout = useCallback(async () => {
    await apiLogout()
    setAuthed(false)
    setUser({})
  }, [])

  const value = useMemo(
    () => ({ ready, authed, user, serverLabel, login, logout, setServerLabel }),
    [ready, authed, user, serverLabel, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth outside provider')
  return ctx
}
