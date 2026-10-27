import { App, Button, Card, Form, Input, Popconfirm, Select, Space, Spin, Switch, Table, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { personaLabel, useCore } from '../hooks/useCore'

type Memory = { id: string; text: string; updated_at?: string }
type Note = { id: string; text: string; updated_at?: string }
type Injection = {
  id: string
  evidence: { kind?: string; text?: string }[]
  char_count?: number
  created_at?: string
  conv_id?: string
}

type Props = {
  /** 角色中心内嵌时锁定人设，并隐藏顶栏选择器 */
  embeddedPersonaId?: string
  hideGlobalSwitches?: boolean
}

export default function MemoriesPage({ embeddedPersonaId, hideGlobalSwitches }: Props = {}) {
  const { message } = App.useApp()
  const { loading, personas, prefs, setPrefs } = useCore()
  const [pid, setPid] = useState<string>()
  const [items, setItems] = useState<Memory[]>([])
  const [notes, setNotes] = useState<Note[]>([])
  const [injections, setInjections] = useState<Injection[]>([])
  const [busy, setBusy] = useState(false)
  const [form] = Form.useForm()
  const [noteForm] = Form.useForm()
  const embedded = !!embeddedPersonaId

  const memoryInject = prefs.memory_inject !== false
  const pauseExtract = !!prefs.pause_memory_extract
  const pauseProactive = !!prefs.pause_proactive
  const quietBlocks = !!prefs.quiet_blocks_chat

  const patchPrefs = async (body: Record<string, unknown>, okMsg: string) => {
    const next = await api<typeof prefs>('/api/v1/prefs', {
      method: 'PUT',
      body: JSON.stringify(body),
    })
    setPrefs(next)
    message.success(okMsg)
  }

  const load = useCallback(
    async (id: string) => {
      setBusy(true)
      try {
        const [list, nlist, inj] = await Promise.all([
          api<Memory[]>(`/api/v1/personas/${id}/memories`),
          api<Note[]>(`/api/v1/personas/${id}/person-notes`),
          api<Injection[]>(`/api/v1/personas/${id}/memory-injections`),
        ])
        setItems(list || [])
        setNotes(nlist || [])
        setInjections(inj || [])
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

  if (loading) return <Spin />
  if (!pid) return <Card>无人设</Card>

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {!hideGlobalSwitches ? (
      <Card
        title="后台开关"
        extra={
          embedded ? null : (
          <Select
            style={{ width: 200 }}
            value={pid}
            options={personas.map((p) => ({ value: p.id, label: personaLabel(p) }))}
            onChange={(v) => {
              setPid(v)
              void load(v)
            }}
          />
          )
        }
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap align="center">
            <Typography.Text style={{ minWidth: 140 }}>主动问候 / 朋友圈</Typography.Text>
            <Switch
              checked={!pauseProactive}
              checkedChildren="运行中"
              unCheckedChildren="已暂停"
              onChange={async (running) => {
                try {
                  await patchPrefs(
                    { pause_proactive: !running },
                    running ? '已开始：定时问候可触发' : '已暂停：不会主动发消息',
                  )
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            />
            <Typography.Text type="secondary">关掉后到点也不发</Typography.Text>
          </Space>
          <Space wrap align="center">
            <Typography.Text style={{ minWidth: 140 }}>自动抽记忆</Typography.Text>
            <Switch
              checked={!pauseExtract}
              checkedChildren="运行中"
              unCheckedChildren="已暂停"
              onChange={async (running) => {
                try {
                  await patchPrefs(
                    { pause_memory_extract: !running },
                    running ? '已开始：聊完会抽事实' : '已暂停：不会再自动写记忆',
                  )
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            />
            <Typography.Text type="secondary">仅影响回合后抽取，手改记忆不受限</Typography.Text>
          </Space>
          <Space wrap align="center">
            <Typography.Text style={{ minWidth: 140 }}>每轮证据注入</Typography.Text>
            <Switch
              checked={memoryInject}
              checkedChildren="开"
              unCheckedChildren="关"
              onChange={async (v) => {
                try {
                  await patchPrefs({ memory_inject: v }, v ? '已开启注入' : '已关闭注入')
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            />
          </Space>
          <Space wrap align="center">
            <Typography.Text style={{ minWidth: 140 }}>免打扰跳过模型</Typography.Text>
            <Switch
              checked={quietBlocks}
              checkedChildren="开"
              unCheckedChildren="关"
              onChange={async (v) => {
                try {
                  await patchPrefs(
                    { quiet_blocks_chat: v },
                    v ? '跳过时段写缓冲' : '免打扰仍可调模型',
                  )
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            />
          </Space>
          <Space wrap>
            <Popconfirm
              title="清空压测填充记忆？"
              description="只删「规模填充记忆…」这类测试数据"
              onConfirm={async () => {
                try {
                  const r = await api<{ deleted: number }>('/api/v1/companion/maintenance/clear-fill-memories', {
                    method: 'POST',
                  })
                  message.success(`已清理 ${r.deleted} 条`)
                  await load(pid)
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            >
              <Button>清理压测记忆</Button>
            </Popconfirm>
            <Button
              onClick={async () => {
                try {
                  const r = await api<{ summary?: string }>(`/api/v1/personas/${pid}/memory-nightly`, {
                    method: 'POST',
                  })
                  message.success(r.summary || '夜间任务完成')
                  await load(pid)
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            >
              跑夜间记忆
            </Button>
          </Space>
        </Space>
      </Card>
      ) : null}

      <Card title="人物档（手动，固定注入）">
        <Form
          form={noteForm}
          layout="inline"
          style={{ marginBottom: 16, display: 'flex', flexWrap: 'wrap', gap: 8 }}
          onFinish={async (v) => {
            try {
              await api(`/api/v1/personas/${pid}/person-notes`, {
                method: 'POST',
                body: JSON.stringify({ text: v.text?.trim() }),
              })
              noteForm.resetFields()
              message.success('已添加人物档')
              await load(pid)
            } catch (e) {
              message.error(e instanceof Error ? e.message : String(e))
            }
          }}
        >
          <Form.Item name="text" rules={[{ required: true, message: '填写人物档' }]} style={{ flex: 1, minWidth: 240 }}>
            <Input placeholder="例如：对方叫小明，在北京读研" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit">
              添加
            </Button>
          </Form.Item>
        </Form>
        <Table
          loading={busy}
          rowKey="id"
          dataSource={notes}
          locale={{ emptyText: '暂无人物档' }}
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
          columns={[
            { title: '内容', dataIndex: 'text' },
            {
              title: '操作',
              width: 90,
              render: (_, m) => (
                <Popconfirm
                  title="删除？"
                  onConfirm={async () => {
                    await api(`/api/v1/personas/${pid}/person-notes/${m.id}`, { method: 'DELETE' })
                    message.success('已删除')
                    await load(pid)
                  }}
                >
                  <Button danger type="link">
                    删除
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
      </Card>

      <Card title={`自动记忆（${items.length}）`}>
        <Form
          form={form}
          layout="inline"
          style={{ marginBottom: 16, display: 'flex', flexWrap: 'wrap', gap: 8 }}
          onFinish={async (v) => {
            try {
              await api(`/api/v1/personas/${pid}/memories`, {
                method: 'POST',
                body: JSON.stringify({ text: v.text?.trim() }),
              })
              form.resetFields()
              message.success('已添加')
              await load(pid)
            } catch (e) {
              message.error(e instanceof Error ? e.message : String(e))
            }
          }}
        >
          <Form.Item name="text" rules={[{ required: true, message: '填写记忆' }]} style={{ flex: 1, minWidth: 240 }}>
            <Input placeholder="例如：主人喜欢安静" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit">
              添加
            </Button>
          </Form.Item>
        </Form>
        <Table
          loading={busy}
          rowKey="id"
          dataSource={items}
          locale={{ emptyText: '暂无记忆' }}
          pagination={{ pageSize: 10 }}
          scroll={{ x: true }}
          columns={[
            { title: '内容', dataIndex: 'text', ellipsis: true },
            {
              title: '更新时间',
              dataIndex: 'updated_at',
              width: 180,
              render: (v?: string) => (v || '').replace('T', ' ').slice(0, 19),
            },
            {
              title: '操作',
              width: 90,
              fixed: 'right',
              render: (_, m) => (
                <Popconfirm
                  title="删除这条记忆？"
                  onConfirm={async () => {
                    await api(`/api/v1/personas/${pid}/memories/${m.id}`, { method: 'DELETE' })
                    message.success('已删除')
                    await load(pid)
                  }}
                >
                  <Button danger type="link">
                    删除
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
      </Card>

      <Card title="本轮注入证据（最近 20）">
        <Table
          loading={busy}
          rowKey="id"
          dataSource={injections}
          locale={{ emptyText: '聊几轮后这里会出现注入快照' }}
          pagination={false}
          columns={[
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 180,
              render: (v?: string) => (v || '').replace('T', ' ').slice(0, 19),
            },
            { title: '字数', dataIndex: 'char_count', width: 80 },
            {
              title: '证据',
              dataIndex: 'evidence',
              render: (ev: Injection['evidence']) =>
                (ev || []).map((e) => `[${e.kind || '?'}] ${e.text || ''}`).join('\n') || '—',
            },
          ]}
        />
      </Card>
    </Space>
  )
}
