import { App, Button, Card, Col, Row, Select, Space, Spin, Switch, Table, Tag, Typography } from 'antd'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { personaLabel, useCore } from '../hooks/useCore'

type Board = {
  seq?: number
  state_version?: number
  affect?: Record<string, number | string>
  affect_events?: { id: string; reason?: string; created_at?: string; deltas_json?: string }[]
  growth?: { mode?: string; growth_epoch?: number; traits?: Record<string, number>; baseline_bio?: string }
  memories?: { id: string; text?: string; status?: string; heat?: number; source_message_id?: string; updated_at?: string }[]
  followups?: { id: string; reason?: string; state?: string; source?: string; created_at?: string }[]
  policy?: { pause_memory_write?: boolean; pause_memory_read?: boolean; memory_policy_version?: number }
}

declare global {
  interface Window {
    echarts?: {
      init: (el: HTMLElement) => {
        setOption: (o: unknown) => void
        dispose: () => void
        resize: () => void
      }
    }
  }
}

function loadEcharts(): Promise<void> {
  if (window.echarts) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = 'https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js'
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('echarts load failed'))
    document.head.appendChild(s)
  })
}

const DIMS = [
  { key: 'joy', label: '愉悦' },
  { key: 'sadness', label: '低落' },
  { key: 'excitement', label: '兴奋' },
  { key: 'worry', label: '担忧' },
  { key: 'annoyance', label: '不悦' },
  { key: 'curiosity', label: '好奇' },
]

type Props = { embeddedPersonaId?: string }

export default function CompanionBoardPage({ embeddedPersonaId }: Props = {}) {
  const { message } = App.useApp()
  const { loading, personas } = useCore()
  const [pid, setPid] = useState<string>()
  const [board, setBoard] = useState<Board | null>(null)
  const [busy, setBusy] = useState(false)
  const radarRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReturnType<NonNullable<typeof window.echarts>['init']> | null>(null)
  const embedded = !!embeddedPersonaId

  const load = useCallback(
    async (id: string) => {
      setBusy(true)
      try {
        const data = await api<Board>(`/api/v1/companion/personas/${id}/board`)
        setBoard(data)
      } catch (e) {
        message.error(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [message],
  )

  useEffect(() => {
    if (embeddedPersonaId) {
      setPid(embeddedPersonaId)
      void load(embeddedPersonaId)
      return
    }
    const id = pid || personas[0]?.id
    if (!id) return
    if (!pid) setPid(id)
    void load(id)
  }, [personas, pid, load, embeddedPersonaId])

  useEffect(() => {
    if (!board?.affect || !radarRef.current) return
    let cancelled = false
    void loadEcharts().then(() => {
      if (cancelled || !radarRef.current || !window.echarts) return
      chartRef.current?.dispose()
      const chart = window.echarts.init(radarRef.current)
      chartRef.current = chart
      const a = board.affect || {}
      chart.setOption({
        title: { text: '六维情绪', left: 'center', textStyle: { fontSize: 14 } },
        tooltip: {},
        radar: {
          indicator: DIMS.map((d) => ({ name: d.label, max: 100 })),
          radius: '65%',
        },
        series: [
          {
            type: 'radar',
            data: [{ value: DIMS.map((d) => Number(a[d.key] ?? 35)), name: '当前' }],
            areaStyle: { opacity: 0.15 },
          },
        ],
      })
    })
    return () => {
      cancelled = true
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [board])

  const savePolicy = async (patch: { pause_memory_write?: boolean; pause_memory_read?: boolean }) => {
    if (!pid) return
    try {
      await api(`/api/v1/companion/personas/${pid}/policy`, {
        method: 'PUT',
        body: JSON.stringify(patch),
      })
      message.success('已更新')
      void load(pid)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const setMode = async (mode: string) => {
    if (!pid) return
    try {
      await api(`/api/v1/companion/personas/${pid}/growth/mode`, {
        method: 'POST',
        body: JSON.stringify({ mode, freeze_current: mode === 'snapshot_locked' }),
      })
      message.success('成长模式已切换')
      void load(pid)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const correctMem = async (id: string, text: string) => {
    if (!pid) return
    const next = window.prompt('纠正为', text)
    if (next == null || !next.trim()) return
    try {
      await api(`/api/v1/companion/personas/${pid}/memories/${id}/correct`, {
        method: 'POST',
        body: JSON.stringify({ text: next.trim() }),
      })
      message.success('已纠正（旧源抑制）')
      void load(pid)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const delMem = async (id: string) => {
    if (!pid) return
    try {
      await api(`/api/v1/companion/personas/${pid}/memories/${id}`, { method: 'DELETE' })
      void load(pid)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  const cancelFu = async (id: string) => {
    if (!pid) return
    try {
      await api(`/api/v1/companion/personas/${pid}/followups/${id}/cancel`, { method: 'POST' })
      void load(pid)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }

  if (loading) return <Spin />
  if (!pid) return <Card>无人设</Card>

  const g = board?.growth
  const pol = board?.policy

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="陪伴看板"
        extra={
          <Space>
            {embedded ? null : (
              <Select
                style={{ width: 200 }}
                value={pid}
                options={personas.map((p) => ({ value: p.id, label: personaLabel(p) }))}
                onChange={(v) => {
                  setPid(v)
                  void load(v)
                }}
              />
            )}
            <Button onClick={() => void load(pid)} loading={busy}>
              刷新
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
          只读图表不唤醒设备、不触发反思。seq={board?.seq ?? '—'} · state_version={board?.state_version ?? '—'}
        </Typography.Paragraph>
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <div ref={radarRef} style={{ height: 320, background: '#fafafa', borderRadius: 8 }} />
          </Col>
          <Col xs={24} md={12}>
            <Typography.Title level={5}>成长</Typography.Title>
            <Space wrap>
              <Tag>模式 {g?.mode || '—'}</Tag>
              <Tag>epoch {g?.growth_epoch ?? '—'}</Tag>
              <Button size="small" onClick={() => void setMode('adaptive')}>
                adaptive
              </Button>
              <Button size="small" onClick={() => void setMode('initial_locked')}>
                initial_locked
              </Button>
              <Button size="small" onClick={() => void setMode('snapshot_locked')}>
                snapshot_locked
              </Button>
            </Space>
            <div style={{ marginTop: 12 }}>
              {Object.entries(g?.traits || {}).map(([k, v]) => (
                <Tag key={k}>
                  {k}:{Math.round(Number(v))}
                </Tag>
              ))}
            </div>
            <Typography.Paragraph style={{ marginTop: 12 }} ellipsis={{ rows: 3 }}>
              baseline：{g?.baseline_bio || '（空）'}
            </Typography.Paragraph>
            <Space>
              <span>暂停写记忆</span>
              <Switch
                checked={!!pol?.pause_memory_write}
                onChange={(v) => void savePolicy({ pause_memory_write: v })}
              />
              <span>暂停读记忆</span>
              <Switch
                checked={!!pol?.pause_memory_read}
                onChange={(v) => void savePolicy({ pause_memory_read: v })}
              />
            </Space>
          </Col>
        </Row>
      </Card>

      <Card title={`记忆（${board?.memories?.length ?? 0}）`}>
        <Table
          size="small"
          rowKey="id"
          pagination={{ pageSize: 8 }}
          dataSource={board?.memories || []}
          columns={[
            { title: '内容', dataIndex: 'text', ellipsis: true },
            { title: '状态', dataIndex: 'status', width: 120, render: (v) => <Tag>{v}</Tag> },
            { title: '热度', dataIndex: 'heat', width: 80, render: (v) => Math.round(Number(v || 0)) },
            {
              title: '来源',
              dataIndex: 'source_message_id',
              width: 120,
              render: (v) => (v ? <Typography.Text code>{String(v).slice(0, 12)}</Typography.Text> : '—'),
            },
            {
              title: '操作',
              width: 160,
              render: (_, r) => (
                <Space>
                  <Button type="link" size="small" onClick={() => void correctMem(r.id, r.text || '')}>
                    纠正
                  </Button>
                  <Button type="link" size="small" danger onClick={() => void delMem(r.id)}>
                    删除
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card title={`跟进（${board?.followups?.length ?? 0}）`}>
        <Table
          size="small"
          rowKey="id"
          pagination={false}
          dataSource={board?.followups || []}
          columns={[
            { title: '原因', dataIndex: 'reason' },
            { title: '状态', dataIndex: 'state', width: 100 },
            { title: '来源', dataIndex: 'source', width: 140 },
            {
              title: '操作',
              width: 100,
              render: (_, r) => (
                <Button type="link" size="small" onClick={() => void cancelFu(r.id)}>
                  取消
                </Button>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  )
}
