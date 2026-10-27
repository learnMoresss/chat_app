import { App, Button, Card, Form, Input, InputNumber, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { personaLabel, useCore } from '../hooks/useCore'

type Schedule = {
  id: string
  persona_id: string
  kind: string
  title: string
  enabled: boolean
  paused: boolean
  hour: number
  minute: number
  next_run_at?: string
  last_run_at?: string
  daily_limit: number
  prompt: string
  require_chatted_today: boolean
}

type Run = {
  id: string
  schedule_id: string
  status: string
  detail: string
  created_at: string
}

type Props = { embeddedPersonaId?: string }

export default function SchedulesPage({ embeddedPersonaId }: Props = {}) {
  const { message } = App.useApp()
  const { personas, loading, prefs, setPrefs } = useCore()
  const [list, setList] = useState<Schedule[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [personaId, setPersonaId] = useState<string>()
  const [form] = Form.useForm()
  const proactiveOn = !prefs.pause_proactive
  const embedded = !!embeddedPersonaId

  const reload = useCallback(async () => {
    try {
      const q = personaId ? `?persona_id=${encodeURIComponent(personaId)}` : ''
      const [s, r] = await Promise.all([
        api<{ schedules: Schedule[] }>(`/api/v1/schedules${q}`),
        api<{ runs: Run[] }>('/api/v1/schedules/runs?limit=40'),
      ])
      setList(s.schedules || [])
      setRuns(r.runs || [])
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

  if (loading) return null

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="定时问候"
        extra={
          embedded ? null : (
          <Select
            style={{ width: 200 }}
            value={personaId}
            options={personas.map((p) => ({ value: p.id, label: personaLabel(p) }))}
            onChange={setPersonaId}
            placeholder="筛选人设"
            allowClear
          />
          )
        }
      >
        <Typography.Paragraph type="secondary">
          到点走正常聊天入口发一句（不落用户气泡）。迟到宽限内只补最近一次。用户说「去睡了 / 去忙了」当晚不再追。
        </Typography.Paragraph>
        <Space wrap align="center" style={{ marginBottom: 16 }}>
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
                message.success(running ? '已开始：定时任务可触发' : '已暂停：不会主动发消息')
                await reload()
              } catch (e) {
                message.error(e instanceof Error ? e.message : String(e))
              }
            }}
          />
          <Typography.Text type="secondary">
            {proactiveOn ? '到点会发问候/朋友圈' : '全部停住，可随时再开'}
          </Typography.Text>
        </Space>
        <Space wrap style={{ marginBottom: 12 }}>
          <Button
            type="primary"
            disabled={!personaId}
            onClick={async () => {
              if (!personaId) return
              try {
                await api('/api/v1/schedules/recipes', {
                  method: 'POST',
                  body: JSON.stringify({ persona_id: personaId, recipe: 'greet_sleep' }),
                })
                message.success('已添加催睡觉')
                await reload()
              } catch (e) {
                message.error(e instanceof Error ? e.message : String(e))
              }
            }}
          >
            一键：催睡觉 23:30
          </Button>
          <Button
            disabled={!personaId}
            onClick={async () => {
              if (!personaId) return
              try {
                await api('/api/v1/schedules/recipes', {
                  method: 'POST',
                  body: JSON.stringify({ persona_id: personaId, recipe: 'greet_morning' }),
                })
                message.success('已添加叫起床')
                await reload()
              } catch (e) {
                message.error(e instanceof Error ? e.message : String(e))
              }
            }}
          >
            一键：叫起床 07:00
          </Button>
          <Button
            disabled={!personaId}
            onClick={async () => {
              if (!personaId) return
              try {
                await api('/api/v1/schedules/recipes', {
                  method: 'POST',
                  body: JSON.stringify({ persona_id: personaId, recipe: 'moment_daily' }),
                })
                message.success('已添加发朋友圈（需人设开启朋友圈）')
                await reload()
              } catch (e) {
                message.error(e instanceof Error ? e.message : String(e))
              }
            }}
          >
            一键：发朋友圈
          </Button>
        </Space>

        <Table
          rowKey="id"
          size="small"
          dataSource={list}
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
              title: '人设',
              dataIndex: 'persona_id',
              render: (id: string) => personas.find((p) => p.id === id)?.name || id.slice(0, 8),
            },
            {
              title: '时间',
              render: (_: unknown, row: Schedule) =>
                `${String(row.hour).padStart(2, '0')}:${String(row.minute).padStart(2, '0')}`,
            },
            {
              title: '下次',
              dataIndex: 'next_run_at',
              render: (v?: string) => (v ? new Date(v).toLocaleString() : '—'),
            },
            {
              title: '启用',
              dataIndex: 'enabled',
              width: 72,
              render: (on: boolean, row: Schedule) => (
                <Switch
                  size="small"
                  checked={on && !row.paused}
                  onChange={async (v) => {
                    try {
                      await api(`/api/v1/schedules/${row.id}`, {
                        method: 'PATCH',
                        body: JSON.stringify({ enabled: v, paused: !v ? row.paused : false }),
                      })
                      await reload()
                    } catch (e) {
                      message.error(e instanceof Error ? e.message : String(e))
                    }
                  }}
                />
              ),
            },
            {
              title: '',
              width: 160,
              render: (_: unknown, row: Schedule) => (
                <Space>
                  <Button
                    size="small"
                    onClick={async () => {
                      try {
                        const r = await api<{ status: string }>(`/api/v1/schedules/${row.id}/run`, { method: 'POST' })
                        message.success(`已触发：${r.status}`)
                        await reload()
                      } catch (e) {
                        message.error(e instanceof Error ? e.message : String(e))
                      }
                    }}
                  >
                    立即跑
                  </Button>
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
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card title="最近执行">
        <Table
          rowKey="id"
          size="small"
          dataSource={runs}
          pagination={false}
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

      <Card title="自定义事件">
        <Form
          form={form}
          layout="vertical"
          style={{ maxWidth: 520 }}
          initialValues={{ hour: 22, minute: 0, jitter_minutes: 10, daily_limit: 1 }}
          onFinish={async (v) => {
            try {
              await api('/api/v1/schedules', {
                method: 'POST',
                body: JSON.stringify({
                  persona_id: v.persona_id,
                  kind: 'custom',
                  title: v.title || '自定义问候',
                  hour: v.hour,
                  minute: v.minute,
                  prompt: v.prompt,
                  jitter_minutes: v.jitter_minutes,
                  daily_limit: v.daily_limit,
                  require_chatted_today: !!v.require_chatted_today,
                }),
              })
              message.success('已创建')
              form.resetFields(['title', 'prompt'])
              await reload()
            } catch (e) {
              message.error(e instanceof Error ? e.message : String(e))
            }
          }}
        >
          <Form.Item name="persona_id" label="人设" rules={[{ required: true }]} initialValue={personaId}>
            <Select options={personas.map((p) => ({ value: p.id, label: personaLabel(p) }))} />
          </Form.Item>
          <Form.Item name="title" label="标题">
            <Input placeholder="例如：催喝水" />
          </Form.Item>
          <Space>
            <Form.Item name="hour" label="时" rules={[{ required: true }]}>
              <InputNumber min={0} max={23} />
            </Form.Item>
            <Form.Item name="minute" label="分" rules={[{ required: true }]}>
              <InputNumber min={0} max={59} />
            </Form.Item>
            <Form.Item name="jitter_minutes" label="抖动(分)">
              <InputNumber min={0} max={60} />
            </Form.Item>
          </Space>
          <Form.Item name="prompt" label="内部指令" rules={[{ required: true }]} extra="只给模型看，不出现在用户气泡">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="require_chatted_today" label="需今天聊过才发" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="daily_limit" label="每日上限">
            <InputNumber min={1} max={5} />
          </Form.Item>
          <Button type="primary" htmlType="submit">
            添加
          </Button>
        </Form>
      </Card>
    </Space>
  )
}
