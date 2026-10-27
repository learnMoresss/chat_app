import { App, Button, Card, Image, Input, Popconfirm, Space, Table, Tag, Typography } from 'antd'
import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, apiBase, getSession } from '../api/client'
import { fmtTime } from '../utils/time'

type StickerRow = {
  id: string
  file_id: string
  label?: string
  emotion?: string
  owner_kind?: string
  created_at?: string
}

type Summary = {
  total: number
  by_label?: { label?: string; emotion?: string; count: number }[]
  by_emotion?: { emotion: string; count: number }[]
}

function AuthThumb({ fileId }: { fileId: string }) {
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
  if (!src) return <div style={{ width: 56, height: 56, background: '#f5f5f5' }} />
  return <Image src={src} width={56} height={56} style={{ objectFit: 'cover' }} />
}

export default function StickersPage() {
  const { message } = App.useApp()
  const [loading, setLoading] = useState(true)
  const [reocring, setReocring] = useState(false)
  const [items, setItems] = useState<StickerRow[]>([])
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [filter, setFilter] = useState('')
  const [filterDraft, setFilterDraft] = useState('')
  const [editing, setEditing] = useState<Record<string, { label: string; emotion: string }>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ limit: '200' })
      if (filter) qs.set('q', filter)
      const [list, sum] = await Promise.all([
        api<{ stickers: StickerRow[]; total: number }>(`/api/v1/stickers?${qs}`),
        api<Summary>('/api/v1/stickers/summary'),
      ])
      setItems(list?.stickers || [])
      setTotal(list?.total ?? 0)
      setSummary(sum)
      const next: Record<string, { label: string; emotion: string }> = {}
      for (const s of list?.stickers || []) {
        next[s.id] = { label: s.label || '', emotion: s.emotion || '' }
      }
      setEditing(next)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [filter, message])

  useEffect(() => {
    void load()
  }, [load])

  const labelTags = useMemo(() => {
    const rows = summary?.by_label || summary?.by_emotion || []
    return rows.map((e) => ({
      tag: (e as { label?: string; emotion?: string }).label || e.emotion || '未标注',
      count: e.count,
    }))
  }, [summary])

  const remove = async (id: string) => {
    try {
      await api(`/api/v1/stickers/${id}`, { method: 'DELETE' })
      message.success('已删除')
      await load()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const save = async (id: string) => {
    const draft = editing[id]
    if (!draft) return
    try {
      await api(`/api/v1/stickers/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ label: draft.label, emotion: draft.emotion }),
      })
      message.success('已更新')
      await load()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const reocr = async () => {
    setReocring(true)
    try {
      const r = await api<{ updated?: number; total?: number }>('/api/v1/stickers/reocr?limit=300', {
        method: 'POST',
      })
      message.success(`OCR 重标完成：${r.updated ?? 0}/${r.total ?? 0}`)
      await load()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setReocring(false)
    }
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="账号表情库"
        extra={
          <Space>
            <Button onClick={() => void reocr()} loading={reocring}>
              OCR 重标全部
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              刷新
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          入库默认 OCR 图上文字作描述（不是含糊情绪）。发送/联想都按描述匹配。旧图可点「OCR 重标全部」。
        </Typography.Paragraph>
        <Space wrap size="middle">
          <Typography.Text>
            合计 <Typography.Text strong>{summary?.total ?? total}</Typography.Text> 张
          </Typography.Text>
          {labelTags.map((e) => (
            <Tag
              key={e.tag}
              color={filter === e.tag ? 'blue' : undefined}
              style={{ cursor: 'pointer' }}
              onClick={() => {
                setFilter(filter === e.tag ? '' : e.tag)
                setFilterDraft(filter === e.tag ? '' : e.tag)
              }}
            >
              {e.tag}: {e.count}
            </Tag>
          ))}
        </Space>
      </Card>

      <Card
        title="表情列表"
        extra={
          <Input.Search
            allowClear
            placeholder="按 OCR 描述筛选"
            style={{ width: 240 }}
            value={filterDraft}
            onChange={(e) => setFilterDraft(e.target.value)}
            onSearch={(v) => setFilter(v.trim())}
          />
        }
      >
        <Table
          rowKey="id"
          loading={loading}
          dataSource={items}
          pagination={{ pageSize: 40, showTotal: (t) => `本页/筛选 ${t} 张` }}
          columns={[
            {
              title: '预览',
              dataIndex: 'file_id',
              width: 80,
              render: (fid: string) => <AuthThumb fileId={fid} />,
            },
            {
              title: '描述（OCR）',
              render: (_, r) => (
                <Input
                  size="small"
                  value={editing[r.id]?.label ?? ''}
                  onChange={(e) =>
                    setEditing((prev) => ({
                      ...prev,
                      [r.id]: { ...(prev[r.id] || { label: '', emotion: '' }), label: e.target.value },
                    }))
                  }
                />
              ),
            },
            {
              title: '短别名',
              width: 120,
              render: (_, r) => (
                <Input
                  size="small"
                  placeholder="可选"
                  value={editing[r.id]?.emotion ?? ''}
                  onChange={(e) =>
                    setEditing((prev) => ({
                      ...prev,
                      [r.id]: { ...(prev[r.id] || { label: '', emotion: '' }), emotion: e.target.value },
                    }))
                  }
                />
              ),
            },
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 160,
              render: (v?: string) => fmtTime(v),
            },
            {
              title: '操作',
              width: 160,
              render: (_, r) => (
                <Space>
                  <Button size="small" onClick={() => void save(r.id)}>
                    保存
                  </Button>
                  <Popconfirm title="从表情库删除？" onConfirm={() => void remove(r.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
          locale={{ emptyText: '暂无表情；收藏后会 OCR 标注图上文字' }}
        />
      </Card>
    </Space>
  )
}
