import {
  App,
  Button,
  Card,
  Form,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Input,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Navigate } from 'react-router-dom'

type Settings = {
  registration_mode: 'open' | 'invite' | 'closed'
  capability_allow_dangerous?: string
  user_count: number
  unused_invites: number
}

type UserRow = {
  id: string
  username: string
  display_name: string
  is_admin: boolean
  is_disabled: boolean
  created_at: string
}

type InviteRow = {
  code: string
  created_at: string
  expires_at?: string | null
  used_by?: string | null
  used_by_username?: string | null
  status: string
}

type CapRow = {
  id: string
  label: string
  description: string
  system_available: boolean
  enabled: boolean
  registered: boolean
}

export default function ServerAdminPage() {
  const { user } = useAuth()
  const { message } = App.useApp()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [users, setUsers] = useState<UserRow[]>([])
  const [invites, setInvites] = useState<InviteRow[]>([])
  const [busy, setBusy] = useState(false)
  const [capUser, setCapUser] = useState<UserRow | null>(null)
  const [caps, setCaps] = useState<CapRow[]>([])
  const [capBusy, setCapBusy] = useState(false)

  const reload = useCallback(async () => {
    setBusy(true)
    try {
      const [s, u, i] = await Promise.all([
        api<Settings>('/api/v1/admin/settings'),
        api<UserRow[]>('/api/v1/admin/users'),
        api<InviteRow[]>('/api/v1/admin/invites'),
      ])
      setSettings(s)
      setUsers(u || [])
      setInvites(i || [])
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [message])

  useEffect(() => {
    if (user.is_admin) void reload()
  }, [user.is_admin, reload])

  if (!user.is_admin) return <Navigate to="/overview" replace />

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card title="注册方式" loading={busy && !settings}>
        <Typography.Paragraph type="secondary">
          公网暴露时请用「邀请码」或「关闭」。开放注册可被扫号开户。登录已限速（IP+用户名 / IP 总量）。
          第一个注册的人自动成为管理员。
        </Typography.Paragraph>
        <Radio.Group
          value={settings?.registration_mode}
          optionType="button"
          buttonStyle="solid"
          options={[
            { label: '开放注册', value: 'open' },
            { label: '邀请码', value: 'invite' },
            { label: '关闭注册', value: 'closed' },
          ]}
          onChange={async (e) => {
            try {
              const next = await api<{ registration_mode: Settings['registration_mode'] }>(
                '/api/v1/admin/settings/registration-mode',
                { method: 'PUT', body: JSON.stringify({ mode: e.target.value }) },
              )
              setSettings((s) => (s ? { ...s, registration_mode: next.registration_mode } : s))
              message.success('已更新注册方式')
            } catch (err) {
              message.error(err instanceof Error ? err.message : String(err))
            }
          }}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          用户 {settings?.user_count ?? 0} · 未用邀请码 {settings?.unused_invites ?? 0}
        </Typography.Paragraph>
      </Card>

      <Card title="高危能力放行（M5 fail-closed）">
        <Typography.Paragraph type="secondary">
          默认拒绝含 shell/exec/adb 等关键词的能力。填写逗号分隔能力 id，或 <Typography.Text code>*</Typography.Text> 全部放行。
        </Typography.Paragraph>
        <Space.Compact style={{ width: '100%', maxWidth: 520 }}>
          <Input
            placeholder="例如 desktop_control,adb 或 *"
            defaultValue={settings?.capability_allow_dangerous || ''}
            key={settings?.capability_allow_dangerous || 'empty'}
            id="cap-allow-dangerous"
          />
          <Button
            type="primary"
            onClick={async () => {
              const el = document.getElementById('cap-allow-dangerous') as HTMLInputElement | null
              const allow = el?.value ?? ''
              try {
                const next = await api<{ capability_allow_dangerous: string }>(
                  '/api/v1/admin/settings/capability-allow-dangerous',
                  { method: 'PUT', body: JSON.stringify({ allow }) },
                )
                setSettings((s) =>
                  s ? { ...s, capability_allow_dangerous: next.capability_allow_dangerous } : s,
                )
                message.success('已更新放行名单')
              } catch (err) {
                message.error(err instanceof Error ? err.message : String(err))
              }
            }}
          >
            保存
          </Button>
        </Space.Compact>
      </Card>

      <Card
        title="邀请码"
        extra={
          <Form
            layout="inline"
            initialValues={{ count: 1, expires_days: 7 }}
            onFinish={async (v) => {
              try {
                await api('/api/v1/admin/invites', {
                  method: 'POST',
                  body: JSON.stringify({
                    count: v.count || 1,
                    expires_days: v.expires_days ?? 7,
                  }),
                })
                message.success('已生成')
                await reload()
              } catch (e) {
                message.error(e instanceof Error ? e.message : String(e))
              }
            }}
          >
            <Form.Item name="count" label="数量">
              <InputNumber min={1} max={20} />
            </Form.Item>
            <Form.Item name="expires_days" label="有效天">
              <InputNumber min={0} max={365} />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit">
                生成
              </Button>
            </Form.Item>
          </Form>
        }
      >
        <Table
          rowKey="code"
          size="small"
          loading={busy}
          dataSource={invites}
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: '邀请码',
              dataIndex: 'code',
              render: (c: string) => <Typography.Text copyable>{c}</Typography.Text>,
            },
            {
              title: '状态',
              dataIndex: 'status',
              render: (s: string) => {
                const color = s === 'unused' ? 'green' : s === 'used' ? 'default' : 'orange'
                const label = s === 'unused' ? '未用' : s === 'used' ? '已用' : '过期'
                return <Tag color={color}>{label}</Tag>
              },
            },
            {
              title: '使用者',
              dataIndex: 'used_by_username',
              render: (v) => v || '—',
            },
            {
              title: '过期',
              dataIndex: 'expires_at',
              render: (v?: string) => (v ? v.replace('T', ' ').slice(0, 19) : '不过期'),
            },
            {
              title: '操作',
              width: 90,
              render: (_, row) =>
                row.status === 'unused' ? (
                  <Popconfirm
                    title="作废此邀请码？"
                    onConfirm={async () => {
                      await api(`/api/v1/admin/invites/${row.code}`, { method: 'DELETE' })
                      message.success('已作废')
                      await reload()
                    }}
                  >
                    <Button danger type="link">
                      作废
                    </Button>
                  </Popconfirm>
                ) : (
                  '—'
                ),
            },
          ]}
        />
      </Card>

      <Card title="用户">
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          能力以本机已部署为准（唱歌/一起听/生图等）；有则默认给子用户启用，管理员可按人关掉。
          没有的能力不会注册进对话，对方要了会如实说还没开通。
        </Typography.Paragraph>
        <Table
          rowKey="id"
          size="middle"
          loading={busy}
          dataSource={users}
          pagination={false}
          columns={[
            { title: '用户名', dataIndex: 'username' },
            { title: '显示名', dataIndex: 'display_name' },
            {
              title: '角色',
              render: (_, r) => (r.is_admin ? <Tag color="blue">管理员</Tag> : <Tag>用户</Tag>),
            },
            {
              title: '状态',
              render: (_, r) =>
                r.is_disabled ? <Tag color="red">已停用</Tag> : <Tag color="green">正常</Tag>,
            },
            {
              title: '注册时间',
              dataIndex: 'created_at',
              render: (v: string) => (v || '').replace('T', ' ').slice(0, 19),
            },
            {
              title: '操作',
              width: 180,
              render: (_, r) => (
                <Space size={0}>
                  <Button
                    type="link"
                    onClick={async () => {
                      setCapUser(r)
                      setCapBusy(true)
                      try {
                        const j = await api<{ capabilities: CapRow[] }>(
                          `/api/v1/admin/users/${r.id}/capabilities`,
                        )
                        setCaps(j.capabilities || [])
                      } catch (e) {
                        message.error(e instanceof Error ? e.message : String(e))
                        setCapUser(null)
                      } finally {
                        setCapBusy(false)
                      }
                    }}
                  >
                    能力
                  </Button>
                  <Popconfirm
                    title={r.is_disabled ? '恢复此账号？' : '停用后会踢掉其所有设备'}
                    onConfirm={async () => {
                      try {
                        await api(`/api/v1/admin/users/${r.id}`, {
                          method: 'PATCH',
                          body: JSON.stringify({ disabled: !r.is_disabled }),
                        })
                        message.success(r.is_disabled ? '已恢复' : '已停用')
                        await reload()
                      } catch (e) {
                        message.error(e instanceof Error ? e.message : String(e))
                      }
                    }}
                  >
                    <Button type="link" danger={!r.is_disabled} disabled={r.id === user.id}>
                      {r.is_disabled ? '恢复' : '停用'}
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={capUser ? `能力 · ${capUser.username}` : '能力'}
        open={!!capUser}
        onCancel={() => setCapUser(null)}
        confirmLoading={capBusy}
        onOk={async () => {
          if (!capUser) return
          setCapBusy(true)
          try {
            const grants: Record<string, boolean> = {}
            for (const c of caps) {
              if (c.system_available) grants[c.id] = c.enabled
            }
            await api(`/api/v1/admin/users/${capUser.id}/capabilities`, {
              method: 'PUT',
              body: JSON.stringify({ grants }),
            })
            message.success('已保存能力开关')
            setCapUser(null)
          } catch (e) {
            message.error(e instanceof Error ? e.message : String(e))
          } finally {
            setCapBusy(false)
          }
        }}
        okText="保存"
        width={560}
      >
        <Typography.Paragraph type="secondary">
          灰掉的项表示本机未部署，无法分配。开关默认开；关掉后对话里不会注册该能力。
        </Typography.Paragraph>
        <Table
          rowKey="id"
          size="small"
          loading={capBusy}
          pagination={false}
          dataSource={caps}
          columns={[
            { title: '能力', dataIndex: 'label', width: 100 },
            {
              title: '本机',
              width: 80,
              render: (_, r) =>
                r.system_available ? <Tag color="green">有</Tag> : <Tag>无</Tag>,
            },
            {
              title: '对该用户启用',
              width: 120,
              render: (_, r) => (
                <Switch
                  checked={r.enabled}
                  disabled={!r.system_available}
                  onChange={(v) =>
                    setCaps((prev) =>
                      prev.map((x) => (x.id === r.id ? { ...x, enabled: v } : x)),
                    )
                  }
                />
              ),
            },
            { title: '说明', dataIndex: 'description', ellipsis: true },
          ]}
        />
      </Modal>
    </Space>
  )
}
