import {
  App,
  Button,
  Card,
  Empty,
  Image,
  Segmented,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  AudioOutlined,
  FileOutlined,
  LinkOutlined,
  PictureOutlined,
  ReloadOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, apiBase, getSession } from '../api/client'
import { fmtTime } from '../utils/time'

type FileRow = {
  id: string
  name?: string
  content_type?: string
  size?: number
  source?: string
  created_at?: string
  auth_url?: string
}

type MediaKind = 'image' | 'video' | 'voice' | 'file'

type FilterKey = 'all' | MediaKind

function kindOf(f: FileRow): MediaKind {
  const ct = (f.content_type || '').toLowerCase()
  const name = (f.name || '').toLowerCase()
  if (ct.startsWith('image/')) return 'image'
  if (ct.startsWith('video/')) return 'video'
  // 聊天语音条：audio/* 或常见语音后缀 / 文件名
  if (
    ct.startsWith('audio/') ||
    name.includes('voice') ||
    /\.(m4a|aac|wav|ogg|mp3)(\b|_)/.test(name) ||
    name.endsWith('.m4a') ||
    name.endsWith('.wav') ||
    name.endsWith('.mp3') ||
    name.endsWith('.aac') ||
    name.endsWith('.ogg')
  ) {
    return 'voice'
  }
  return 'file'
}

function sourceLabel(source?: string, kind?: MediaKind): string {
  if (kind === 'voice') return '聊天语音'
  switch ((source || '').toLowerCase()) {
    case 'chat':
      return '聊天上传'
    case 'sticker_url':
    case 'sticker':
      return '表情包'
    case 'persona':
    case 'mood':
      return '人设/示意'
    case 'plugin':
    case 'download':
      return '下载/插件'
    case 'moments':
      return '朋友圈'
    default:
      return source || '其它'
  }
}

function fmtSize(s?: number) {
  if (!s) return '—'
  if (s < 1024) return `${s} B`
  if (s < 1024 * 1024) return `${Math.round(s / 1024)} KB`
  return `${(s / (1024 * 1024)).toFixed(1)} MB`
}

function prettyName(f: FileRow, kind: MediaKind) {
  const raw = (f.name || '').trim()
  if (kind === 'voice') return '语音消息'
  if (kind === 'image') return raw.replace(/^mf_[a-z0-9]+_/i, '') || '图片'
  if (kind === 'video') return raw.replace(/^mf_[a-z0-9]+_/i, '') || '视频'
  return raw.replace(/^mf_[a-z0-9]+_/i, '') || f.id.slice(0, 12)
}

function AuthThumb({
  fileId,
  kind,
  size = 120,
}: {
  fileId: string
  kind: MediaKind
  size?: number
}) {
  const [src, setSrc] = useState('')
  useEffect(() => {
    if (kind !== 'image' && kind !== 'video') return
    let revoked = ''
    let cancelled = false
    ;(async () => {
      try {
        const token = getSession().token
        const res = await fetch(`${apiBase()}/api/v1/media/files/${fileId}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!res.ok || cancelled) return
        const blob = await res.blob()
        // 视频不直接当缩略图播：只给图片预览
        if (kind === 'video' && !blob.type.startsWith('image/')) {
          return
        }
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
  }, [fileId, kind])

  const box = {
    width: size,
    height: size,
    borderRadius: 8,
    background: '#f5f5f5',
    display: 'flex' as const,
    alignItems: 'center' as const,
    justifyContent: 'center' as const,
    overflow: 'hidden' as const,
    flexShrink: 0,
  }

  if (kind === 'image' && src) {
    return (
      <Image
        src={src}
        width={size}
        height={size}
        style={{ objectFit: 'cover', borderRadius: 8 }}
        preview={{ mask: '预览' }}
      />
    )
  }

  if (kind === 'video') {
    return (
      <div style={{ ...box, background: '#1f1f1f', color: '#fff' }}>
        <VideoCameraOutlined style={{ fontSize: 28 }} />
      </div>
    )
  }
  if (kind === 'voice') {
    return (
      <div style={{ ...box, background: '#e8f5e9', color: '#2e7d32', width: 56, height: 56 }}>
        <AudioOutlined style={{ fontSize: 22 }} />
      </div>
    )
  }
  return (
    <div style={{ ...box, width: 56, height: 56, color: '#8c8c8c' }}>
      {kind === 'image' ? <PictureOutlined style={{ fontSize: 22 }} /> : <FileOutlined style={{ fontSize: 22 }} />}
    </div>
  )
}

function VoicePlayer({ fileId }: { fileId: string }) {
  const [src, setSrc] = useState('')
  useEffect(() => {
    let revoked = ''
    let cancelled = false
    ;(async () => {
      try {
        const token = getSession().token
        const res = await fetch(`${apiBase()}/api/v1/media/files/${fileId}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!res.ok || cancelled) return
        const blob = await res.blob()
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
  if (!src) return <Typography.Text type="secondary">加载中…</Typography.Text>
  // 原生控件，明确是「文件播放」，不是聊天气泡波形
  return <audio controls preload="metadata" src={src} style={{ width: '100%', maxWidth: 280, height: 32 }} />
}

export default function MediaPage() {
  const { message } = App.useApp()
  const [loading, setLoading] = useState(true)
  const [files, setFiles] = useState<FileRow[]>([])
  const [filter, setFilter] = useState<FilterKey>('image')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api<{ files: FileRow[] }>('/api/v1/media/files?limit=200')
      setFiles(data?.files || [])
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    void load()
  }, [load])

  const enriched = useMemo(
    () =>
      files.map((f) => {
        const kind = kindOf(f)
        return { ...f, kind, title: prettyName(f, kind), sourceText: sourceLabel(f.source, kind) }
      }),
    [files],
  )

  const counts = useMemo(() => {
    const c = { all: enriched.length, image: 0, video: 0, voice: 0, file: 0 }
    for (const f of enriched) c[f.kind] += 1
    return c
  }, [enriched])

  const visible = useMemo(
    () => (filter === 'all' ? enriched : enriched.filter((f) => f.kind === filter)),
    [enriched, filter],
  )

  const share = async (fileId: string) => {
    try {
      const res = await api<{ url: string }>('/api/v1/shares', {
        method: 'POST',
        body: JSON.stringify({ file_id: fileId }),
      })
      await navigator.clipboard.writeText(res.url)
      message.success('外链已复制')
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  if (loading && files.length === 0) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin />
      </div>
    )
  }

  return (
    <Card
      title="媒体库"
      extra={
        <Space>
          <Button
            onClick={async () => {
              try {
                const r = await api<{ hashed?: number; collapsed?: number; freed_bytes?: number }>(
                  '/api/v1/media/dedup',
                  { method: 'POST' },
                )
                message.success(
                  `去重完成：补 hash ${r.hashed ?? 0}，折叠 ${r.collapsed ?? 0}，释放 ${Math.round((r.freed_bytes || 0) / 1024)} KB`,
                )
                await load()
              } catch (e) {
                message.error(e instanceof Error ? e.message : String(e))
              }
            }}
          >
            去重整理
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
        按类型浏览；同内容默认只显示一条。上传/生图/表情入库均按 sha256 去重，避免重复占盘。
      </Typography.Paragraph>

      <Segmented
        value={filter}
        onChange={(v) => setFilter(v as FilterKey)}
        options={[
          { label: `图片 ${counts.image}`, value: 'image' },
          { label: `视频 ${counts.video}`, value: 'video' },
          { label: `语音 ${counts.voice}`, value: 'voice' },
          { label: `文件 ${counts.file}`, value: 'file' },
          { label: `全部 ${counts.all}`, value: 'all' },
        ]}
        style={{ marginBottom: 16 }}
      />

      {visible.length === 0 ? (
        <Empty description={filter === 'voice' ? '暂无聊天语音' : '暂无此类媒体'} />
      ) : filter === 'image' || (filter === 'all' && visible.some((v) => v.kind === 'image')) ? (
        <>
          {(filter === 'image' || filter === 'all') && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
                gap: 12,
                marginBottom: filter === 'all' ? 20 : 0,
              }}
            >
              {visible
                .filter((f) => f.kind === 'image')
                .map((f) => (
                  <div
                    key={f.id}
                    style={{
                      border: '1px solid #f0f0f0',
                      borderRadius: 10,
                      padding: 8,
                      background: '#fff',
                    }}
                  >
                    <AuthThumb fileId={f.id} kind="image" size={124} />
                    <div style={{ marginTop: 8 }}>
                      <Typography.Text ellipsis style={{ display: 'block', fontSize: 12 }}>
                        {f.title}
                      </Typography.Text>
                      <Space size={4} wrap style={{ marginTop: 4 }}>
                        <Tag style={{ margin: 0 }}>{f.sourceText}</Tag>
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                          {fmtSize(f.size)}
                        </Typography.Text>
                      </Space>
                      <Button
                        size="small"
                        type="link"
                        icon={<LinkOutlined />}
                        style={{ paddingInline: 0, marginTop: 2 }}
                        onClick={() => void share(f.id)}
                      >
                        外链
                      </Button>
                    </div>
                  </div>
                ))}
            </div>
          )}
          {filter === 'all'
            ? visible
                .filter((f) => f.kind !== 'image')
                .map((f) => <MediaRow key={f.id} f={f} onShare={share} />)
            : null}
        </>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {visible.map((f) => (
            <MediaRow key={f.id} f={f} onShare={share} />
          ))}
        </div>
      )}
    </Card>
  )
}

function MediaRow({
  f,
  onShare,
}: {
  f: FileRow & { kind: MediaKind; title: string; sourceText: string }
  onShare: (id: string) => void
}) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 12,
        alignItems: 'center',
        padding: '10px 12px',
        border: '1px solid #f0f0f0',
        borderRadius: 10,
        background: f.kind === 'voice' ? '#fafafa' : '#fff',
      }}
    >
      <AuthThumb fileId={f.id} kind={f.kind} size={f.kind === 'video' ? 72 : 56} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <Space wrap size={6}>
          <Typography.Text strong>{f.title}</Typography.Text>
          <Tag color={f.kind === 'voice' ? 'green' : f.kind === 'video' ? 'purple' : 'default'}>
            {f.kind === 'voice' ? '聊天语音' : f.kind === 'video' ? '视频' : '文件'}
          </Tag>
          <Tag>{f.sourceText}</Tag>
        </Space>
        <div style={{ marginTop: 4 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {fmtTime(f.created_at)} · {fmtSize(f.size)}
            {f.content_type ? ` · ${f.content_type}` : ''}
          </Typography.Text>
        </div>
        {f.kind === 'voice' ? (
          <div style={{ marginTop: 8 }}>
            <VoicePlayer fileId={f.id} />
          </div>
        ) : null}
      </div>
      <Tooltip title="复制外链">
        <Button size="small" icon={<LinkOutlined />} onClick={() => void onShare(f.id)}>
          外链
        </Button>
      </Tooltip>
    </div>
  )
}
