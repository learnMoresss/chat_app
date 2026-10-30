import { App, Alert, Button, Card, Form, Input, InputNumber, Space, Switch, Table, Tag, Typography } from 'antd'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { useCore } from '../hooks/useCore'

type Schedule = {
  id: string
  persona_id: string
  kind: string
  title: string
  enabled: boolean
  paused: boolean
  hour: number
  minute: number
  next_run_at?: string | null
  prompt?: string
}

type Run = {
  id: string
  schedule_id: string
  status: string
  detail: string
  created_at: string
}

type Props = { embeddedPersonaId?: string }

function isUserReminder(kind: string) {
  return kind === 'user_once' || kind === 'user_daily'
}

function isLegacy(kind: string) {
  return /^(greet_|moment_)/.test(kind) || kind === 'moment_daily'
}

function fmtNext(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString()
}

/** 人设维度：聊天提醒 / 每日任务；另保留全局主动开关与旧配方清理。 */
export default function SchedulesPage({ embeddedPersonaId }: Props = {}) {
  const { message } = App.useApp()
  const { personas, loading, prefs, setPrefs } = useCore()
  const [list, setList] = useState<Schedule[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [personaId, setPersonaId] = useState<string>()
  const [cleaning, setCleaning] = useState(false)
  const [adding, setAdding] = useState(false)
  const [form] = Form.useForm<{ title: string; hour: number; minute: number }>()
  const proactiveOn = !prefs.pause_proactive
  const embedded = !!embeddedPersonaId

  const reload = useCallback(async () => {
    if (!personaId) {
      setList([])
      setRuns([])
      return
    }
    try {
      const q = `?persona_id=${encodeURIComponent(personaId)}`
      const [s, r] = await Promise.all([
        api<{ schedules: Schedule[] }>(`/api/v1/schedules${q}`),
        api<{ runs: Run[] }>(`/api/v1/schedules/runs?limit=40`),
      ])
      setList(s.schedules || [])
      // 执行记录接口无 persona 过滤：只留当前人设任务相关
      const ids = new Set((s.schedules || []).map((x) => x.id))
      setRuns((r.runs || []).filter((row) => ids.has(row.schedule_id)))
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    }
  }, [message, personaId])

  useEffect(() => {
    if (!loading) void reload()
  }, [loading, reload])

  useEffect(() => {
    if (embeddedPersonaId) {
      setPersonaId(embeddedPersonaId)
      return
    }
    if (!personaId && personas[0]) setPersonaId(personas[0].id)
  }, [personas, personaId, embeddedPersonaId])

  const reminders = useMemo(() => list.filter((s) => isUserReminder(s.kind)), [list])
  const legacy = useMemo(() => list.filter((s) => isLegacy(s.kind)), [list])
  const personaName = personas.find((p) => p.id === personaId)?.name || personaId?.slice(0, 8) || '—'

  if (loading) return null

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title={`聊天提醒（人设：${personaName}）`}
        extra={
          !embedded ? (
            <Space>
              <Typography.Text type="secondary">切换人设</Typography.Text>
              <select
                value={personaId || ''}
                onChange={(e) => setPersonaId(e.target.value || undefined)}
                style={{ minWidth: 140 }}
              >
                {personas.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Space>
          ) : null
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="提醒按人设隔离，不是全局任务"
          description="在该人设聊天里说「每天8点提醒我」「一小时后和我说」「现在有什么任务」会写入此人设；全局「主动推送」暂停不会关掉这些提醒。"
        />
        <Table
          rowKey="id"
          size="small"
          dataSource={reminders}
          pagination={false}
          locale={{ emptyText: '此人设暂无聊天提醒' }}
          columns={[
            {
              title: '标题',
              dataIndex: 'title',
              render: (t: string, row: Schedule) => (
                <Space>
                  <span>{t || '提醒'}</span>
                  <Tag color={row.kind === 'user_daily' ? 'blue' : 'purple'}>
                    {row.kind === 'user_daily' ? '每天' : '一次'}
                  </Tag>
                </Space>
              ),
            },
            {
              title: '时间',
              render: (_: unknown, row: Schedule) =>
                row.kind === 'user_daily'
                  ? `每天 ${String(row.hour).padStart(2, '0')}:${String(row.minute).padStart(2, '0')}`
                  : fmtNext(row.next_run_at),
            },
            {
              title: '下次',
              dataIndex: 'next_run_at',
              render: (v: string | null) => fmtNext(v),
            },
            {
              title: '状态',
              render: (_: unknown, row: Schedule) =>
                row.enabled && !row.paused ? <Tag color="green">运行</Tag> : <Tag>停用</Tag>,
            },
            {
              title: '',
              width: 160,
              render: (_: unknown, row: Schedule) => (
                <Space>
                  <Button
                    size="small"
                    onClick={async () => {
                      const on = row.enabled && !row.paused
                      try {
                        await api(`/api/v1/schedules/${row.id}`, {
                          method: 'PATCH',
                          body: JSON.stringify({ enabled: !on, paused: on, reschedule: !on }),
                        })
                        await reload()
                      } catch (e) {
                        message.error(e instanceof Error ? e.message : String(e))
                      }
                    }}
                  >
                    {row.enabled && !row.paused ? '停用' : '启用'}
                  </Button>
                  <Button
                    size="small"
                    danger
                    onClick={async () => {
                      try {
                        await api(`/api/v1/schedules/${row.id}`, { method: 'DELETE' })
                        message.success('已删除')
                        await reload()
                      } catch (e) {
                        message.error(e instanceof Error ? e.message : String(e))
                      }
                    }}
                  >
                    删
                  </Button>
                </Space>
              ),
            },
          ]}
        />

        <Card type="inner" title="给此人设添加每日提醒" style={{ marginTop: 16 }}>
          <Form
            form={form}
            layout="inline"
            initialValues={{ hour: 8, minute: 0, title: '' }}
            onFinish={async (vals) => {
              if (!personaId) return
              setAdding(true)
              try {
                const title = (vals.title || '').trim() || '提醒一下'
                await api('/api/v1/schedules', {
                  method: 'POST',
                  body: JSON.stringify({
                    persona_id: personaId,
                    kind: 'user_daily',
                    title,
                    hour: vals.hour,
                    minute: vals.minute,
                    prompt: `到点了，用平常聊天语气提醒对方：${title}。一两句话就好，不要说这是定时任务。`,
                    jitter_minutes: 0,
                    daily_limit: 1,
                    require_chatted_today: false,
                    min_interval_minutes: 0,
                    enabled: true,
                  }),
                })
                form.resetFields(['title'])
                message.success('已写入此人设')
                await reload()
              } catch (e) {
                message.error(e instanceof Error ? e.message : String(e))
              } finally {
                setAdding(false)
              }
            }}
          >
            <Form.Item name="title" rules={[{ required: true, message: '填内容' }]}>
              <Input placeholder="提醒内容" style={{ width: 180 }} />
            </Form.Item>
            <Form.Item name="hour" rules={[{ required: true }]}>
              <InputNumber min={0} max={23} addonAfter="时" />
            </Form.Item>
            <Form.Item name="minute" rules={[{ required: true }]}>
              <InputNumber min={0} max={59} addonAfter="分" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" loading={adding}>
                添加
              </Button>
            </Form.Item>
          </Form>
        </Card>
      </Card>

      {!embedded ? (
        <Card title="全局主动推送（不含聊天提醒）">
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="这是账号级主动开关"
            description="只影响机会驱动/旧主动类任务。人设聊天提醒（user_once / user_daily）不受此开关影响。"
          />
          <Space wrap align="center">
            <Typography.Text strong>全局主动任务</Typography.Text>
            <Switch
              checked={proactiveOn}
              checkedChildren="运行中"
              unCheckedChildren="已暂停"
              onChange={async (running) => {
                try {
                  const next = await api<typeof prefs>('/api/v1/prefs', {
                    method: 'PUT',
                    body: JSON.stringify({ pause_proactive: !running }),
                  })
                  setPrefs(next)
                  message.success(running ? '已开启主动推送' : '已暂停主动推送（聊天提醒仍会响）')
                  await reload()
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            />
          </Space>
        </Card>
      ) : null}

      {legacy.length > 0 ? (
        <Card
          title={`旧定时配方（此人设可删，${legacy.length}）`}
          extra={
            <Button
              danger
              loading={cleaning}
              onClick={async () => {
                setCleaning(true)
                try {
                  for (const row of legacy) {
                    await api(`/api/v1/schedules/${row.id}`, { method: 'DELETE' })
                  }
                  message.success(`已清理 ${legacy.length} 条旧定时`)
                  await reload()
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                } finally {
                  setCleaning(false)
                }
              }}
            >
              一键清掉
            </Button>
          }
        >
          <Table
            rowKey="id"
            size="small"
            dataSource={legacy}
            pagination={false}
            columns={[
              {
                title: '标题',
                dataIndex: 'title',
                render: (t: string, row: Schedule) => (
                  <Space>
                    <span>{t}</span>
                    <Tag>{row.kind}</Tag>
                  </Space>
                ),
              },
              {
                title: '时间',
                render: (_: unknown, row: Schedule) =>
                  `${String(row.hour).padStart(2, '0')}:${String(row.minute).padStart(2, '0')}`,
              },
              {
                title: '',
                width: 80,
                render: (_: unknown, row: Schedule) => (
                  <Button
                    size="small"
                    danger
                    onClick={async () => {
                      try {
                        await api(`/api/v1/schedules/${row.id}`, { method: 'DELETE' })
                        await reload()
                      } catch (e) {
                        message.error(e instanceof Error ? e.message : String(e))
                      }
                    }}
                  >
                    删
                  </Button>
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      <Card title={`此人设最近执行（${runs.length}）`}>
        <Table
          rowKey="id"
          size="small"
          dataSource={runs}
          pagination={false}
          locale={{ emptyText: '暂无' }}
          columns={[
            { title: '时间', dataIndex: 'created_at', render: (v: string) => new Date(v).toLocaleString() },
            {
              title: '结果',
              dataIndex: 'status',
              render: (s: string) => (
                <Tag color={s === 'ok' ? 'green' : s === 'skipped' ? 'default' : 'red'}>{s}</Tag>
              ),
            },
            { title: '说明', dataIndex: 'detail', ellipsis: true },
          ]}
        />
      </Card>
    </Space>
  )
}
