import { Alert, App, Button, Card, Segmented, Select, Space, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useCore } from '../hooks/useCore'

type VDevice = {
  id: string
  label: string
  kind: string
  backend: string
  status: string
  view_url: string
  created_at: string
  user_id?: string
  username?: string
  owner_persona_id?: string
  meta?: { reason?: string }
}

type ListResp = {
  devices?: VDevice[]
  items?: VDevice[]
  all?: VDevice[]
  all_count?: number
  scope?: string
  user_id?: string
}

export default function VirtualDevicesPage() {
  const { message } = App.useApp()
  const { user } = useAuth()
  const { personas } = useCore()
  const isAdmin = !!user.is_admin
  const [rows, setRows] = useState<VDevice[]>([])
  const [mine, setMine] = useState<VDevice[]>([])
  const [all, setAll] = useState<VDevice[]>([])
  const [busy, setBusy] = useState(false)
  const [scope, setScope] = useState<'mine' | 'all'>(isAdmin ? 'all' : 'mine')
  const [personaId, setPersonaId] = useState('')

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const j = await api<ListResp>('/api/v1/virtual-devices')
      const my = j.devices || j.items || []
      const everyone = j.all || []
      setMine(my)
      setAll(everyone)
      setRows(isAdmin && scope === 'all' ? everyone : my)
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [message, isAdmin, scope])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    setRows(isAdmin && scope === 'all' ? all : mine)
  }, [scope, isAdmin, all, mine])

  const stats = useMemo(() => {
    const src = isAdmin && scope === 'all' ? all : mine
    const desk = src.filter((d) => d.kind !== 'android')
    const phone = src.filter((d) => d.kind === 'android')
    return {
      desk: desk.length,
      phone: phone.length,
      running: src.filter((d) => d.status === 'running').length,
    }
  }, [all, mine, isAdmin, scope])

  const create = async (kind: 'desktop' | 'android') => {
    if (!personaId) {
      message.warning('请先选择要绑定的人设（设备一人设一台，不会默认创建）')
      return
    }
    try {
      setBusy(true)
      await api('/api/v1/virtual-devices', {
        method: 'POST',
        body: JSON.stringify({
          label: kind === 'android' ? '虚拟手机' : '虚拟电脑',
          kind,
          persona_id: personaId,
        }),
      })
      message.success(kind === 'android' ? '已为人设创建虚拟手机' : '已为人设创建虚拟电脑')
      setScope('mine')
      await load()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="虚拟设备（电脑 / 手机）"
        extra={
          <Space wrap>
            {isAdmin ? (
              <Segmented
                value={scope}
                onChange={(v) => setScope(v as 'mine' | 'all')}
                options={[
                  { label: `全站 ${all.length}`, value: 'all' },
                  { label: `我的 ${mine.length}`, value: 'mine' },
                ]}
              />
            ) : null}
            <Select
              placeholder="绑定人设"
              style={{ minWidth: 140 }}
              value={personaId || undefined}
              onChange={(v) => setPersonaId(v)}
              options={personas.map((p) => ({
                value: p.id,
                label: p.remark || p.name || p.id.slice(0, 8),
              }))}
              allowClear
            />
            <Button type="primary" loading={busy} onClick={() => void create('desktop')}>
              新建电脑
            </Button>
            <Button loading={busy} onClick={() => void create('android')}>
              新建手机
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={busy}>
              刷新
            </Button>
          </Space>
        }
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
          设备必须绑定人设，一人设每种一台；新人设不默认创建。AI 要用时会在聊天发「申请创建」卡片，你批准后才建。
        </Typography.Paragraph>

        <Space wrap size={8} style={{ marginBottom: 12 }}>
          <Tag>电脑 {stats.desk}</Tag>
          <Tag>手机 {stats.phone}</Tag>
          <Tag color={stats.running ? 'green' : 'default'}>运行中 {stats.running}</Tag>
        </Space>

        {rows.length === 0 ? (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              isAdmin && scope === 'all'
                ? '全站还没有未销毁的虚拟设备'
                : '当前登录账号下还没有设备实例'
            }
            description={
              isAdmin && scope === 'all' && mine.length === 0 && all.length === 0
                ? '说明文案里的 docker_novnc / redroid 是后端能力，不是已创建的实例。点「新建电脑 / 新建手机」才会出现在列表。'
                : isAdmin && scope === 'mine' && all.length > 0
                  ? `全站有 ${all.length} 台设备，但不属于当前账号。可切到「全站」查看，或点新建挂到本账号。`
                  : '点「新建电脑 / 新建手机」创建实例。'
            }
          />
        ) : null}

        <Table
          rowKey="id"
          loading={busy}
          dataSource={rows}
          pagination={{ pageSize: 20, size: 'small' }}
          columns={[
            ...(isAdmin && scope === 'all'
              ? [
                  {
                    title: '账号',
                    dataIndex: 'username',
                    width: 100,
                    render: (v: string, r: VDevice) => v || r.user_id?.slice(0, 10) || '—',
                  },
                ]
              : []),
            { title: '标签', dataIndex: 'label' },
            {
              title: '人设',
              dataIndex: 'owner_persona_id',
              width: 120,
              ellipsis: true,
              render: (v?: string) => {
                if (!v) return <Typography.Text type="secondary">未绑定</Typography.Text>
                const p = personas.find((x) => x.id === v)
                return p?.remark || p?.name || v.slice(0, 10)
              },
            },
            {
              title: '类型',
              dataIndex: 'kind',
              width: 80,
              render: (k: string) =>
                k === 'android' ? <Tag color="blue">手机</Tag> : <Tag>电脑</Tag>,
            },
            { title: '后端', dataIndex: 'backend', width: 120 },
            {
              title: '状态',
              dataIndex: 'status',
              width: 110,
              render: (s: string, r) => (
                <Space size={4}>
                  <Tag color={s === 'running' ? 'green' : s === 'stopped' ? 'default' : 'orange'}>
                    {s}
                  </Tag>
                  {r.meta?.reason ? (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {r.meta.reason}
                    </Typography.Text>
                  ) : null}
                </Space>
              ),
            },
            {
              title: '观看',
              dataIndex: 'view_url',
              ellipsis: true,
              render: (u: string) =>
                u && !u.includes('view.local') ? (
                  <Typography.Link href={u} target="_blank" rel="noreferrer">
                    打开画面
                  </Typography.Link>
                ) : (
                  <Typography.Text type="secondary">{u || '—'}</Typography.Text>
                ),
            },
            {
              title: '操作',
              width: 220,
              render: (_, r) => {
                // 全站视图里别人的设备：只允许看，操作仍走原 API（按 JWT 会 404），故仅对本账号或 mine 视图操作
                const canOp = scope === 'mine' || r.user_id === user.id || !r.user_id
                if (!canOp) {
                  return <Typography.Text type="secondary">属其它账号</Typography.Text>
                }
                return (
                  <Space size={4} wrap>
                    {r.status !== 'running' ? (
                      <Button
                        size="small"
                        onClick={async () => {
                          try {
                            await api(`/api/v1/virtual-devices/${r.id}/start`, { method: 'POST' })
                            await load()
                          } catch (e) {
                            message.error(e instanceof Error ? e.message : String(e))
                          }
                        }}
                      >
                        启动
                      </Button>
                    ) : (
                      <Button
                        size="small"
                        onClick={async () => {
                          try {
                            await api(`/api/v1/virtual-devices/${r.id}/stop?force=1`, {
                              method: 'POST',
                            })
                            await load()
                          } catch (e) {
                            message.error(e instanceof Error ? e.message : String(e))
                          }
                        }}
                      >
                        停止
                      </Button>
                    )}
                    <Button
                      size="small"
                      danger
                      onClick={async () => {
                        try {
                          await api(`/api/v1/virtual-devices/${r.id}?force=1`, { method: 'DELETE' })
                          message.success('已销毁')
                          await load()
                        } catch (e) {
                          message.error(e instanceof Error ? e.message : String(e))
                        }
                      }}
                    >
                      销毁
                    </Button>
                  </Space>
                )
              },
            },
          ]}
          locale={{ emptyText: '无设备' }}
        />
      </Card>
    </Space>
  )
}
