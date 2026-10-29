import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'

export type Persona = {
  id: string
  name: string
  remark?: string
  bio?: string
  color?: number
  channel_id?: string
  greet?: boolean
  moments_on?: boolean
  worldline_enabled?: boolean
  is_bot?: boolean
  avatar_file_id?: string
}

export type Channel = {
  id: string
  name: string
  model: string
  base_url: string
  key_filled: boolean
  shared: boolean
  owner?: string
  api_key?: string
}

export type Prefs = {
  active_persona_id?: string | null
  shape_enabled?: boolean
  split_send?: boolean
  echo_streak?: number
  quiet_blocks_chat?: boolean
  memory_inject?: boolean
  pause_proactive?: boolean
  pause_memory_extract?: boolean
}

export function useCore() {
  const { setServerLabel } = useAuth()
  const [loading, setLoading] = useState(true)
  const [personas, setPersonas] = useState<Persona[]>([])
  const [channels, setChannels] = useState<Channel[]>([])
  const [prefs, setPrefs] = useState<Prefs>({})
  const [error, setError] = useState('')

  const reload = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = !!opts?.silent
    if (!silent) setLoading(true)
    setError('')
    try {
      const [info, p, c, pr] = await Promise.all([
        api<{ name: string; version: string }>('/api/info'),
        api<Persona[]>('/api/v1/personas'),
        api<Channel[]>('/api/v1/channels'),
        api<Prefs>('/api/v1/prefs'),
      ])
      setPersonas(p || [])
      setChannels(c || [])
      setPrefs(pr || {})
      setServerLabel(`${info.name} · v${info.version}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      if (!silent) throw e
    } finally {
      if (!silent) setLoading(false)
    }
  }, [setServerLabel])

  useEffect(() => {
    void reload()
  }, [reload])

  return { loading, error, personas, channels, prefs, setPrefs, setPersonas, setChannels, reload }
}

export function personaLabel(p: Persona) {
  return p.remark || p.name
}
