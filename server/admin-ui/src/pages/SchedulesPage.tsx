import { App, Alert, Button, Card, Space, Switch, Table, Tag, Typography } from 'antd'
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
  next_run_at?: string
}

type Run = {
  id: string
  schedule_id: string
  status: string
  detail: string
  created_at: string
}

type Props = { embeddedPersonaId?: string }

/** 固定钟点问候已退役；本页只保留全局开关、清理旧配方、查看最近执行。 */
export default function SchedulesPage({ embeddedPersonaId }: Props = {}) {
  const { message } = App.useApp()
  const { personas, loading, prefs, setPrefs } = useCore()
  const [list, setList] = useState<Schedule[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [personaId, setPersonaId] = useState<string>()
  const [cleaning, setCleaning] = useState(false)
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

  const legacy = useMemo(
    () => list.filter((s) => /^(greet_|moment_)/.test(s.kind) || s.kind === 'moment_daily'),
    [list],
  )

  if (loading) return null

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card title="主动推送">
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="固定钟点问候已退役"
          description="叫起床 / 催睡觉 / 定点朋友圈不再按表走。改由世界线、心情、作息窗口、久未联系等机会驱动；请到「陪伴」「世界线」看候选与门禁。这里只保留全局开关。"
        />
        <Space wrap align="center" style={{ marginBottom: 8 }}>
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
                message.success(running ? '已开启：机会驱动可推送' : '已暂停：不会主动发消息')
                await reload()
              } catch (e) {
                message.error(e instanceof Error ? e.message : String(e))
              }
            }}
          />
          <Typography.Text type="secondary">
            {proactiveOn ? '规划器可按世界线/心情推送' : '全部停住，可随时再开'}
          </Typography.Text>
        </Space>
        {!embedded && personaId ? (
          <Typography.Text type="secondary">
            当前筛选：{personas.find((p) => p.id === personaId)?.name || personaId.slice(0, 8)}
          </Typography.Text>
        ) : null}
      </Card>

      {legacy.length > 0 ? (
        <Card
          title="旧定时配方（可删）"
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
          <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
            这些是退役前留下的钟点任务，即使启用也不会再按旧逻辑轰炸；建议直接删掉。
          </Typography.Paragraph>
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

      <Card title="最近执行（遗留记录）">
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
