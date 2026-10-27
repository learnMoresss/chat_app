import { App, Button, Card, Col, Row, Select, Space, Spin, Statistic, Switch, Tag, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, apiBase } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useCore } from '../hooks/useCore'

type TunnelStatus = {
  provider?: string
  active?: boolean
  public_url?: string
  error?: string
  providers?: { id: string; name: string; binary_installed?: boolean }[]
}

export default function OverviewPage() {
  const nav = useNavigate()
  const { message } = App.useApp()
  const { user } = useAuth()
  const { loading, personas, channels, prefs, setPrefs } = useCore()
  const [sum, setSum] = useState<{ calls?: number; ok_calls?: number; total_tokens?: number }>({})
  const [tunnel, setTunnel] = useState<TunnelStatus | null>(null)
  const [provider, setProvider] = useState('cloudflared')
  const [busy, setBusy] = useState(false)
  const [health, setHealth] = useState<{
    dsh_configured?: boolean
    tunnel_active?: boolean
    tunnel_public_url?: string
  } | null>(null)
  const [wakeBufs, setWakeBufs] = useState<{ conv_id: string; count: number; latest_reason?: string }[]>([])

  const reloadTunnel = useCallback(async () => {
    try {
      if (user.is_admin) {
        const t = await api<TunnelStatus>('/api/v1/admin/tunnel')
        setTunnel(t)
        if (t.provider && t.provider !== 'none') setProvider(t.provider)
      } else {
        const r = await fetch(apiBase() + '/api/tunnel')
        setTunnel((await r.json()) as TunnelStatus)
      }
    } catch {
      setTunnel(null)
    }
  }, [user.is_admin])

  useEffect(() => {
    api<typeof sum>('/api/v1/usage/summary?days=30').then(setSum).catch(() => {})
    fetch(apiBase() + '/health')
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => null)
    void reloadTunnel()
    api<{ items: typeof wakeBufs }>('/api/v1/admin/wake-buffers')
      .then((r) => setWakeBufs(r.items || []))
      .catch(() => setWakeBufs([]))
  }, [reloadTunnel])

  const active = personas.find((p) => p.id === prefs.active_persona_id)

  if (loading) return <Spin />

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title="近 30 天调用" value={sum.calls || 0} />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title="成功" value={sum.ok_calls || 0} />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title="Token" value={sum.total_tokens || 0} />
          </Card>
        </Col>
      </Row>

      <Card title="主脑 / 健康">
        <Tag color={health?.dsh_configured ? 'blue' : 'red'}>
          DSH {health?.dsh_configured ? '已配置' : '未配置'}
        </Tag>
        <Tag color={health?.tunnel_active ? 'blue' : 'default'} style={{ marginLeft: 8 }}>
          隧道 {health?.tunnel_active ? '活跃' : '关'}
        </Tag>
        <Button size="small" style={{ marginLeft: 8 }} onClick={() => nav('/health')}>
          网关健康
        </Button>
        {health?.tunnel_public_url ? (
          <Typography.Paragraph copyable type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
            {health.tunnel_public_url}
          </Typography.Paragraph>
        ) : null}
      </Card>

      <Card title="后台任务（可随时暂停 / 开始）">
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap align="center">
            <Typography.Text style={{ minWidth: 140 }}>主动问候 / 朋友圈</Typography.Text>
            <Switch
              checked={!prefs.pause_proactive}
              checkedChildren="运行中"
              unCheckedChildren="已暂停"
              onChange={async (running) => {
                try {
                  const next = await api<typeof prefs>('/api/v1/prefs', {
                    method: 'PUT',
                    body: JSON.stringify({ pause_proactive: !running }),
                  })
                  setPrefs(next)
                  message.success(running ? '已开始' : '已暂停')
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            />
          </Space>
          <Space wrap align="center">
            <Typography.Text style={{ minWidth: 140 }}>自动抽记忆</Typography.Text>
            <Switch
              checked={!prefs.pause_memory_extract}
              checkedChildren="运行中"
              unCheckedChildren="已暂停"
              onChange={async (running) => {
                try {
                  const next = await api<typeof prefs>('/api/v1/prefs', {
                    method: 'PUT',
                    body: JSON.stringify({ pause_memory_extract: !running }),
                  })
                  setPrefs(next)
                  message.success(running ? '已开始' : '已暂停')
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                }
              }}
            />
          </Space>
          <Typography.Text type="secondary">
            不用时关掉即可，不会自己偷偷跑。需要时再打开。
          </Typography.Text>
        </Space>
      </Card>

      <Card title="唤醒缓冲（跳过只记）">
        {wakeBufs.length === 0 ? (
          <Typography.Text type="secondary">暂无未消费缓冲</Typography.Text>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {wakeBufs.map((b) => (
              <li key={b.conv_id}>
                {b.conv_id.slice(0, 12)}… · {b.count} 条 · {b.latest_reason || '—'}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="公网穿透（M1）">
        <Space wrap size={8} style={{ marginBottom: 8 }}>
          <Tag color={tunnel?.active ? 'green' : 'default'}>
            {tunnel?.provider || 'none'}
            {tunnel?.active ? ' · 活跃' : ' · 未穿透'}
          </Tag>
          {tunnel?.error ? <Tag color="orange">{tunnel.error}</Tag> : null}
        </Space>
        {tunnel?.public_url ? (
          <Typography.Paragraph copyable style={{ marginBottom: 8 }}>
            {tunnel.public_url}
          </Typography.Paragraph>
        ) : (
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            未检测到公网 URL。管理员可在下方启动 ngrok / cloudflared（需本机已安装二进制与 token）。
          </Typography.Paragraph>
        )}
        {user.is_admin ? (
          <Space wrap>
            <Select
              style={{ width: 180 }}
              value={provider}
              onChange={setProvider}
              options={(tunnel?.providers || [])
                .filter((p) => p.id !== 'none')
                .map((p) => ({
                  value: p.id,
                  label: `${p.name}${p.binary_installed === false ? '（未安装）' : ''}`,
                }))}
            />
            <Button
              type="primary"
              loading={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  const st = await api<TunnelStatus>('/api/v1/admin/tunnel/start', {
                    method: 'POST',
                    body: JSON.stringify({ provider }),
                  })
                  setTunnel(st)
                  if (st.public_url) message.success('穿透已启动，请把 URL 填进 App')
                  else message.warning(st.error || '已启动但未解析到 URL')
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                } finally {
                  setBusy(false)
                  await reloadTunnel()
                }
              }}
            >
              启动
            </Button>
            <Button
              loading={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  await api('/api/v1/admin/tunnel/stop', { method: 'POST' })
                  message.success('已停止')
                } catch (e) {
                  message.error(e instanceof Error ? e.message : String(e))
                } finally {
                  setBusy(false)
                  await reloadTunnel()
                }
              }}
            >
              停止
            </Button>
            <Button onClick={() => void reloadTunnel()}>刷新</Button>
          </Space>
        ) : null}
        <div style={{ marginTop: 12 }}>
          <Button size="small" onClick={() => nav('/virtual-devices')}>
            虚拟电脑
          </Button>
        </div>
      </Card>

      <Card title="快捷入口">
        <Space wrap>
          <Button onClick={() => nav('/partner')}>默认聊天</Button>
          <Button type="primary" onClick={() => nav('/personas')}>
            角色中心
          </Button>
          <Button onClick={() => nav('/personas?tab=memory')}>记忆</Button>
          <Button onClick={() => nav('/personas?tab=extract')}>提取</Button>
          <Button onClick={() => nav('/channels')}>模型 Key</Button>
          <Button onClick={() => nav('/ops?tab=virtual')}>虚拟电脑</Button>
          <Button onClick={() => nav('/account?tab=devices')}>登录设备</Button>
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
          当前默认聊天对象：
          <Typography.Text strong>{active?.name || '未设置'}</Typography.Text>
          {' · '}人设 {personas.length} 个 · 渠道 {channels.length} 条
        </Typography.Paragraph>
      </Card>
    </Space>
  )
}
