import { useEffect, useState } from 'react'
import { UserOutlined } from '@ant-design/icons'
import { apiBase, getSession } from '../api/client'

type Props = {
  fileId?: string
  /** 无图时的色块兜底（ARGB int 或 #rrggbb） */
  fallbackColor?: number | string
  size?: number
  radius?: number | string
  alt?: string
}

function toCssColor(c?: number | string) {
  if (c == null || c === '') return '#d9d9d9'
  if (typeof c === 'string') return c.startsWith('#') ? c : `#${c}`
  return `#${(Number(c) & 0xffffff).toString(16).padStart(6, '0')}`
}

/** 带 Bearer 拉媒体缩略图；失败则色块 / 默认头像。 */
export default function AuthMediaThumb({
  fileId,
  fallbackColor,
  size = 40,
  radius = '50%',
  alt = '',
}: Props) {
  const [src, setSrc] = useState('')

  useEffect(() => {
    const fid = (fileId || '').trim()
    if (!fid) {
      setSrc('')
      return
    }
    let revoked = ''
    let cancelled = false
    ;(async () => {
      try {
        const token = getSession().token
        const res = await fetch(`${apiBase()}/api/v1/media/files/${fid}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!res.ok || cancelled) return
        const blob = await res.blob()
        if (!blob.type.startsWith('image/') && blob.size < 32) return
        revoked = URL.createObjectURL(blob)
        if (!cancelled) setSrc(revoked)
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [fileId])

  const box = {
    width: size,
    height: size,
    borderRadius: radius,
    overflow: 'hidden' as const,
    flexShrink: 0,
    display: 'flex' as const,
    alignItems: 'center' as const,
    justifyContent: 'center' as const,
    background: src ? '#f5f5f5' : toCssColor(fallbackColor),
    border: '1px solid rgba(0,0,0,0.06)',
  }

  if (src) {
    return (
      <div style={box}>
        <img src={src} alt={alt} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      </div>
    )
  }

  return (
    <div style={box}>
      {!fallbackColor ? <UserOutlined style={{ color: '#fff', fontSize: Math.max(14, size * 0.4) }} /> : null}
    </div>
  )
}
