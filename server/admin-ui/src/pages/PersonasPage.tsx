import { App, Button, Card, Col, Form, Input, Modal, Popconfirm, Row, Select, Space, Spin, Switch, Table, Tabs, Typography, Upload } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import AuthMediaThumb from '../components/AuthMediaThumb'
import { personaLabel, useCore, type Persona } from '../hooks/useCore'
import MemoriesPage from './MemoriesPage'
import CompanionBoardPage from './CompanionBoardPage'
import SchedulesPage from './SchedulesPage'
import ExtractPage from './ExtractPage'
import WorldlineMockPage from './WorldlineMockPage'

type Eraser = {
  id: string
  pattern: string
  mode: string
  enabled: boolean
  hits: number
}

const COLORS = [
  0xff2f6bff, 0xffe6a23c, 0xff9b59b6, 0xff16a085, 0xffe67e22, 0xffe74c3c, 0xff34495e, 0xff07c160,
]

const TAB_KEYS = ['profile', 'memory', 'companion', 'schedules', 'extract', 'worldline'] as const
type TabKey = (typeof TAB_KEYS)[number]

function colorHex(n: number) {
  return `#${(n & 0xffffff).toString(16).padStart(6, '0')}`
}

export default function PersonasPage() {
  const { message } = App.useApp()
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const { loading, personas, channels, setPersonas, reload } = useCore()
  const [pid, setPid] = useState<string>()
  const [form] = Form.useForm()
  const [creating, setCreating] = useState(false)
  const [createForm] = Form.useForm()
  const [erasers, setErasers] = useState<Eraser[]>([])
  const [eraserForm] = Form.useForm()
  const [avatarId, setAvatarId] = useState('')
  const detailRef = useRef<HTMLDivElement>(null)

  const tabParam = (params.get('tab') || 'profile') as TabKey
  const tab: TabKey = TAB_KEYS.includes(tabParam) ? tabParam : 'profile'

  const scrollDetailToTop = useCallback(() => {
    const root = detailRef.current
    if (root) root.scrollTop = 0
  }, [])

  const current = useMemo(
    () => personas.find((p) => p.id === pid) || personas[0],
    [personas, pid],
  )

  const loadErasers = useCallback(async (id: string) => {
    try {
      const list = await api<Eraser[]>(`/api/v1/personas/${id}/erasers`)
      setErasers(list)
    } catch {
      setErasers([])
    }
  }, [])

  useEffect(() => {
    const fromQuery = params.get('persona')
    if (fromQuery && personas.some((p) => p.id === fromQuery)) {
      setPid(fromQuery)
      return
    }
    if (!pid && personas[0]) setPid(personas[0].id)
  }, [personas, params, pid])

  useEffect(() => {
    if (!current) return
    setPid(current.id)
    form.setFieldsValue({
      name: current.name,
      remark: current.remark || '',
      bio: current.bio || '',
      channel_id: current.channel_id || undefined,
      greet: !!current.greet,
      moments_on: !!current.moments_on,
      worldline_enabled: current.worldline_enabled !== false,
      color: current.color || COLORS[0],
    })
    setAvatarId(current.avatar_file_id || '')
    void loadErasers(current.id)
  }, [current, form, loadErasers])

  const selectPersona = (id: string, opts?: { scrollTop?: boolean }) => {
    setPid(id)
    const next = new URLSearchParams(params)
    next.set('persona', id)
    setParams(next, { replace: true })
    if (opts?.scrollTop !== false) {
      // 等右侧表单切完再滚，避免仍停在橡皮擦底部
      requestAnimationFrame(() => scrollDetailToTop())
    }
  }

  useEffect(() => {
    scrollDetailToTop()
  }, [pid, scrollDetailToTop])

  const setTab = (key: string) => {
    const next = new URLSearchParams(params)
    next.set('tab', key)
    if (current?.id) next.set('persona', current.id)
    setParams(next, { replace: true })
  }

  if (loading) return <Spin />

  const profilePane = !current ? (
    <Card>还没有角色，请先新建。</Card>
  ) : (
    <Card title={`资料 · ${current.name}`} styles={{ body: { width: '100%' } }}>
      <Form
        form={form}
        layout="vertical"
        onFinish={async (v) => {
          try {
            const updated = await api<Persona>(`/api/v1/personas/${current.id}`, {
              method: 'PATCH',
              body: JSON.stringify({
                name: v.name?.trim(),
                remark: v.remark?.trim(),
                bio: v.bio,
                channel_id: v.channel_id || '',
                greet: !!v.greet,
                moments_on: !!v.moments_on,
                worldline_enabled: v.worldline_enabled !== false,
                color: v.color,
                avatar_file_id: avatarId,
              }),
            })
            setAvatarId(updated.avatar_file_id || '')
            setPersonas((list) => list.map((x) => (x.id === updated.id ? updated : x)))
            message.success('已保存')
            await reload()
          } catch (e) {
            message.error(e instanceof Error ? e.message : String(e))
          }
        }}
      >
        <Row gutter={16}>
          <Col xs={24} sm={12}>
            <Form.Item label="名字" name="name" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item label="备注" name="remark">
              <Input />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item label="头像">
          <Space align="start" size={16}>
            <AuthMediaThumb
              fileId={avatarId}
              fallbackColor={form.getFieldValue('color') || current.color || COLORS[0]}
              size={72}
              radius={12}
              alt={current.name}
            />
            <div>
              <Upload
                accept="image/*"
                showUploadList={false}
                customRequest={async ({ file, onSuccess, onError }) => {
                  try {
                    const fd = new FormData()
                    fd.append('file', file as File)
                    fd.append('kind', 'image')
                    const up = await api<{ file_id: string }>('/api/v1/media/chat-upload', {
                      method: 'POST',
                      body: fd,
                    })
                    setAvatarId(up.file_id)
                    onSuccess?.(up)
                    message.success('头像已上传，点保存生效')
                  } catch (e) {
                    onError?.(e as Error)
                    message.error(e instanceof Error ? e.message : String(e))
                  }
                }}
              >
                <Button>上传头像</Button>
              </Upload>
              {avatarId ? (
                <Button
                  type="link"
                  size="small"
                  danger
                  style={{ paddingInline: 0, display: 'block' }}
                  onClick={() => setAvatarId('')}
                >
                  清除头像（用颜色块）
                </Button>
              ) : (
                <Typography.Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
                  未设置图片时用下方颜色
                </Typography.Text>
              )}
            </div>
          </Space>
        </Form.Item>
        <Form.Item label="头像颜色（无图时）" name="color">
          <Select
            options={COLORS.map((c) => ({
              value: c,
              label: (
                <Space>
                  <span style={{ display: 'inline-block', width: 16, height: 16, borderRadius: 3, background: colorHex(c) }} />
                  {colorHex(c)}
                </Space>
              ),
            }))}
          />
        </Form.Item>
        <Form.Item label="人设说明（bio）" name="bio" extra="会写进系统提示词，决定 TA 怎么说话">
          <Input.TextArea rows={6} />
        </Form.Item>
        <Form.Item label="渠道" name="channel_id" extra="没填 Key 的渠道聊不了天">
          <Select
            allowClear
            placeholder="自动选有 Key 的"
            options={channels.map((c) => ({
              value: c.id,
              label: `${c.name}${c.key_filled ? '' : ' · 无Key'}`,
            }))}
          />
        </Form.Item>
        <Space size={24} style={{ marginBottom: 16 }} wrap>
          <Form.Item name="greet" label="自己问候" valuePropName="checked" style={{ marginBottom: 0 }}>
            <Switch />
          </Form.Item>
          <Form.Item name="moments_on" label="朋友圈" valuePropName="checked" style={{ marginBottom: 0 }}>
            <Switch />
          </Form.Item>
          <Form.Item
            name="worldline_enabled"
            label="世界线"
            valuePropName="checked"
            style={{ marginBottom: 0 }}
            tooltip="关闭后仅对话驱动，无天气/日程/扰动"
          >
            <Switch />
          </Form.Item>
        </Space>
        <Form.Item>
          <Space wrap>
            <Button type="primary" htmlType="submit">
              保存资料
            </Button>
            <Popconfirm
              title={`删除角色「${current.name}」？`}
              description="将删除人设、聊天记录与记忆，不可恢复。"
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
              onConfirm={async () => {
                const deleted = current
                const snapshot = personas
                const rest = snapshot.filter((p) => p.id !== deleted.id)
                // 乐观更新：先从列表拿掉，不整页 loading
                setPersonas(rest)
                const nextId = rest[0]?.id
                if (nextId) {
                  selectPersona(nextId)
                } else {
                  setPid(undefined)
                  const next = new URLSearchParams(params)
                  next.delete('persona')
                  setParams(next, { replace: true })
                }
                try {
                  await api(`/api/v1/personas/${deleted.id}`, { method: 'DELETE' })
                  message.success('已删除')
                } catch (e) {
                  setPersonas(snapshot)
                  selectPersona(deleted.id)
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            >
              <Button danger>删除此角色</Button>
            </Popconfirm>
          </Space>
        </Form.Item>
      </Form>

      <Typography.Title level={5} style={{ marginTop: 8 }}>
        橡皮擦（出站）
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        删这句 / 删这个词；命中后不会漏回原文。
      </Typography.Paragraph>
      <Form
        form={eraserForm}
        layout="inline"
        style={{ marginBottom: 12, flexWrap: 'wrap', gap: 8 }}
        onFinish={async (v) => {
          if (!current) return
          try {
            await api(`/api/v1/personas/${current.id}/erasers`, {
              method: 'POST',
              body: JSON.stringify({
                pattern: v.pattern?.trim(),
                mode: v.mode || 'word',
                enabled: true,
              }),
            })
            eraserForm.resetFields()
            await loadErasers(current.id)
            message.success('已添加')
          } catch (e) {
            message.error(e instanceof Error ? e.message : String(e))
          }
        }}
      >
        <Form.Item name="pattern" rules={[{ required: true, message: '填要删的内容' }]}>
          <Input placeholder="删这个词 / 句" style={{ width: 180 }} />
        </Form.Item>
        <Form.Item name="mode" initialValue="word">
          <Select
            style={{ width: 110 }}
            options={[
              { value: 'word', label: '删词' },
              { value: 'sentence', label: '删句' },
              { value: 'regex', label: '正则' },
            ]}
          />
        </Form.Item>
        <Form.Item>
          <Button type="dashed" htmlType="submit" icon={<PlusOutlined />}>
            添加
          </Button>
        </Form.Item>
      </Form>
      <Table
        size="small"
        rowKey="id"
        pagination={false}
        dataSource={erasers}
        columns={[
          { title: '内容', dataIndex: 'pattern', ellipsis: true },
          {
            title: '模式',
            dataIndex: 'mode',
            width: 80,
            render: (m: string) => ({ word: '删词', sentence: '删句', regex: '正则' }[m] || m),
          },
          { title: '命中', dataIndex: 'hits', width: 64 },
          {
            title: '启用',
            dataIndex: 'enabled',
            width: 72,
            render: (on: boolean, row: Eraser) => (
              <Switch
                size="small"
                checked={on}
                onChange={async (v) => {
                  if (!current) return
                  try {
                    await api(`/api/v1/personas/${current.id}/erasers/${row.id}`, {
                      method: 'PATCH',
                      body: JSON.stringify({ enabled: v }),
                    })
                    await loadErasers(current.id)
                  } catch (e) {
                    message.error(e instanceof Error ? e.message : String(e))
                  }
                }}
              />
            ),
          },
          {
            title: '',
            width: 64,
            render: (_: unknown, row: Eraser) => (
              <Popconfirm
                title="删除这条规则？"
                onConfirm={async () => {
                  if (!current) return
                  try {
                    await api(`/api/v1/personas/${current.id}/erasers/${row.id}`, { method: 'DELETE' })
                    await loadErasers(current.id)
                  } catch (e) {
                    message.error(e instanceof Error ? e.message : String(e))
                  }
                }}
              >
                <Button type="link" danger size="small">
                  删
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
    </Card>
  )

  return (
    <div
      className="personas-split"
      style={{
        flex: 1,
        minHeight: 0,
        height: '100%',
        display: 'flex',
        gap: 16,
        overflow: 'hidden',
        alignItems: 'stretch',
      }}
    >
      <div
        style={{
          width: 'min(300px, 28%)',
          flex: '0 0 auto',
          minWidth: 220,
          maxWidth: 320,
          minHeight: 0,
          overflow: 'auto',
          alignSelf: 'stretch',
        }}
      >
        <Card
          title="角色"
          extra={
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={() => {
                createForm.setFieldsValue({
                  name: '',
                  bio: '你是一个温柔、简短的聊天对象。用口语说话，不要列点，不要提自己是 AI。',
                  channel_id: channels.find((c) => c.key_filled)?.id,
                  color: COLORS[0],
                  greet: false,
                  moments_on: false,
                  worldline_enabled: true,
                })
                setCreating(true)
              }}
            >
              新建
            </Button>
          }
          styles={{ body: { padding: 12 } }}
        >
          {personas.length === 0 ? (
            <Typography.Text type="secondary">还没有角色。</Typography.Text>
          ) : (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              {personas.map((p) => {
                const active = p.id === current?.id
                return (
                  <div
                    key={p.id}
                    onClick={() => selectPersona(p.id)}
                    style={{
                      cursor: 'pointer',
                      padding: '10px 12px',
                      borderRadius: 8,
                      border: `1px solid ${active ? '#93c5fd' : '#e2e8f0'}`,
                      background: active ? '#eff6ff' : '#fff',
                    }}
                  >
                    <Space>
                      <AuthMediaThumb
                        fileId={p.avatar_file_id}
                        fallbackColor={p.color || COLORS[0]}
                        size={36}
                        radius={8}
                        alt={p.name}
                      />
                      <Typography.Text strong>{personaLabel(p)}</Typography.Text>
                    </Space>
                    <Typography.Paragraph
                      type="secondary"
                      ellipsis={{ rows: 2 }}
                      style={{ marginBottom: 0, marginTop: 4, fontSize: 12 }}
                    >
                      {p.bio || '暂无 bio'}
                    </Typography.Paragraph>
                  </div>
                )
              })}
            </Space>
          )}
          <Button type="link" style={{ paddingLeft: 0, marginTop: 8 }} onClick={() => nav('/partner')}>
            设置默认聊天对象 →
          </Button>
        </Card>
      </div>

      <div
        style={{
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          overflow: 'hidden',
          alignSelf: 'stretch',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Card
          className="personas-detail-card"
          style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
          styles={{
            body: {
              flex: 1,
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
              paddingTop: 8,
              width: '100%',
            },
          }}
        >
          <Tabs
            className="personas-detail-tabs"
            activeKey={tab}
            onChange={setTab}
            items={[
              { key: 'profile', label: '资料' },
              { key: 'memory', label: '记忆' },
              { key: 'companion', label: '陪伴' },
              { key: 'schedules', label: '主动' },
              { key: 'extract', label: '提取' },
              { key: 'worldline', label: '世界线' },
            ]}
          />
          <div ref={detailRef} className="personas-detail-scroll">
            {tab === 'profile' ? profilePane : null}
            {tab === 'memory' ? (
              current ? (
                <MemoriesPage embeddedPersonaId={current.id} hideGlobalSwitches />
              ) : (
                <Typography.Text type="secondary">先选角色</Typography.Text>
              )
            ) : null}
            {tab === 'companion' ? (
              current ? (
                <CompanionBoardPage embeddedPersonaId={current.id} />
              ) : (
                <Typography.Text type="secondary">先选角色</Typography.Text>
              )
            ) : null}
            {tab === 'schedules' ? (
              current ? (
                <SchedulesPage embeddedPersonaId={current.id} />
              ) : (
                <Typography.Text type="secondary">先选角色</Typography.Text>
              )
            ) : null}
            {tab === 'extract' ? (
              current ? (
                <ExtractPage embeddedPersonaId={current.id} />
              ) : (
                <Typography.Text type="secondary">先选角色</Typography.Text>
              )
            ) : null}
            {tab === 'worldline' ? (
              current ? (
                <WorldlineMockPage
                  personaId={current.id}
                  personaName={personaLabel(current)}
                  worldlineEnabled={current.worldline_enabled !== false}
                />
              ) : (
                <Typography.Text type="secondary">先选角色</Typography.Text>
              )
            ) : null}
          </div>
        </Card>
      </div>

      <Modal
        title="新建角色"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => createForm.submit()}
        okText="创建"
        destroyOnHidden
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={async (v) => {
            try {
              const created = await api<Persona>('/api/v1/personas', {
                method: 'POST',
                body: JSON.stringify({
                  name: v.name?.trim(),
                  bio: v.bio || '',
                  color: v.color || COLORS[0],
                  channel_id: v.channel_id || '',
                  is_bot: true,
                  greet: !!v.greet,
                  moments_on: !!v.moments_on,
                  worldline_enabled: v.worldline_enabled !== false,
                }),
              })
              message.success(`已创建「${created.name}」`)
              setCreating(false)
              await reload()
              selectPersona(created.id)
            } catch (e) {
              message.error(e instanceof Error ? e.message : String(e))
            }
          }}
        >
          <Form.Item label="名字" name="name" rules={[{ required: true, message: '请填名字' }]}>
            <Input placeholder="例如：深深" />
          </Form.Item>
          <Form.Item label="头像颜色" name="color">
            <Select
              options={COLORS.map((c) => ({
                value: c,
                label: (
                  <Space>
                    <span style={{ display: 'inline-block', width: 16, height: 16, borderRadius: 3, background: colorHex(c) }} />
                    {colorHex(c)}
                  </Space>
                ),
              }))}
            />
          </Form.Item>
          <Form.Item label="人设说明" name="bio">
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item label="渠道" name="channel_id">
            <Select
              allowClear
              options={channels.map((c) => ({
                value: c.id,
                label: `${c.name}${c.key_filled ? '' : ' · 无Key'}`,
              }))}
            />
          </Form.Item>
          <Space size={24} wrap>
            <Form.Item name="greet" label="自己问候" valuePropName="checked" style={{ marginBottom: 0 }}>
              <Switch />
            </Form.Item>
            <Form.Item name="moments_on" label="朋友圈" valuePropName="checked" style={{ marginBottom: 0 }}>
              <Switch />
            </Form.Item>
            <Form.Item name="worldline_enabled" label="世界线" valuePropName="checked" style={{ marginBottom: 0 }}>
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  )
}
